"""Connection ownership helpers for long, non-database waits (Phase 1P.9R).

An ``AsyncSession`` keeps its pooled connection from its first statement until
the transaction ends. Code that reads, then waits seconds on a model or
embedding call, then writes, therefore pins a pooled connection for the whole
wait. Under concurrency that starves the pool (and, combined with a second
connection checked out for a lock, deadlocks it until the pool timeout).

``release_idle_connection`` ends the session's transaction *only when it has
written nothing*, returning the connection to the pool before the wait. The
next statement transparently checks a connection out again. Sessions are
created with ``expire_on_commit=False``, so loaded objects stay usable. A
transaction that has flushed or pending writes, or has taken row locks or
transaction-scoped advisory locks, is never committed early - the helper leaves
it (and its connection) alone - so transactional consistency is never traded
for pool capacity.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import Select, TextClause, event, inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session

_WROTE = "cxops_txn_wrote"


@event.listens_for(Session, "after_flush")
def _mark_written(session: Session, _flush_context: object) -> None:
    session.info[_WROTE] = True


@event.listens_for(Session, "do_orm_execute")
def _mark_locking_reads(orm_execute_state: ORMExecuteState) -> None:
    # A transaction holding row locks (SELECT ... FOR UPDATE) or
    # transaction-scoped advisory locks must never be ended early: committing
    # it would silently release those locks.
    if _takes_transaction_locks(orm_execute_state.statement):
        orm_execute_state.session.info[_WROTE] = True


def _takes_transaction_locks(statement: object) -> bool:
    if isinstance(statement, TextClause):
        sql = statement.text.lower()
        return "for update" in sql or "for share" in sql or "xact_lock" in sql
    if isinstance(statement, Select):
        if statement._for_update_arg is not None:
            return True
        return any(
            getattr(column, "name", "").endswith("xact_lock")
            for column in statement.selected_columns
        )
    return False


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _clear_written(session: Session) -> None:
    session.info.pop(_WROTE, None)


def has_uncommitted_writes(db: AsyncSession) -> bool:
    sync = db.sync_session
    return bool(sync.new or sync.dirty or sync.deleted or sync.info.get(_WROTE))


async def release_idle_connection(db: AsyncSession) -> bool:
    """Return ``db``'s connection to the pool if its transaction is read-only.

    Returns ``True`` when a connection was released.
    """
    if not isinstance(db, AsyncSession):
        return False  # not a real session (e.g. a test double): nothing to release
    if not db.in_transaction() or has_uncommitted_writes(db):
        return False
    await db.commit()
    return True


async def refresh_if_stale(db: AsyncSession, instance: object) -> None:
    """``db.refresh(instance)`` only when something on it is actually stale.

    Repositories ``commit()`` and then ``refresh()`` the row they just wrote so
    server-generated columns (ids, timestamps) are populated. With
    ``eager_defaults`` enabled on every mapper those values come back in the
    INSERT/UPDATE's own ``RETURNING`` and, with ``expire_on_commit=False``,
    nothing is expired, so the follow-up SELECT is a pure network round trip
    (a measurable cost per statement against a remote database). If any
    attribute is expired - after a rollback, or a column the dialect could not
    return - the full refresh still happens, so the helper can never leave an
    object less loaded than ``refresh`` would have.
    """
    state = inspect(instance)
    if state is not None and not state.expired and not state.expired_attributes:
        return
    await db.refresh(instance)


@asynccontextmanager
async def dedicated_lock_connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A pooled connection used only to hold session-level advisory locks.

    On exit every advisory lock it holds is released before it returns to the
    pool; if that cannot be confirmed (cancellation, broken connection) the
    connection is invalidated, which ends its server session and releases the
    locks there. A blocking ``pg_advisory_lock`` cancelled just as the server
    grants it can therefore never leak a lock into the pool.
    """
    conn = await engine.connect()
    try:
        yield conn
    finally:
        try:
            await conn.rollback()
            await conn.execute(text("SELECT pg_advisory_unlock_all()"))
            await conn.commit()
        except BaseException as exc:
            await conn.invalidate()
            if not isinstance(exc, Exception):
                raise  # cancellation / interpreter exit: propagate
        finally:
            await conn.close()


def pool_capacity(engine: object) -> int | None:
    """Connections the engine's pool can hand out, or ``None`` if unbounded."""
    pool = getattr(getattr(engine, "sync_engine", engine), "pool", None)
    size = getattr(pool, "size", None)
    overflow = getattr(pool, "_max_overflow", None)
    if not callable(size) or overflow is None:
        return None  # NullPool / StaticPool: no pool-level bound
    if overflow < 0:
        return None  # unlimited overflow
    return int(size()) + int(overflow)


class AnalysisCapacity:
    """Bounds concurrent persisted analyses per process.

    Each analysis pins one dedicated lock connection for its duration (its
    session connection is released around model calls). Pinned connections
    must leave room for the in-process turn dispatcher's lease connections
    and for at least two request connections, or a burst can deadlock the
    pool (every connection held by someone waiting for another). The limit
    is therefore ``min(configured, pool - dispatcher turns - 2)``, at least 1,
    computed from the live engine when first used in an event loop.
    """

    def __init__(self, configured: int) -> None:
        self.configured = configured
        # One semaphore per event loop (asyncio primitives bind to a loop).
        self._semaphores: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Semaphore
        ] = weakref.WeakKeyDictionary()
        self.limit = configured
        self.active = 0
        self.peak = 0

    def effective_limit(self) -> int:
        from app.core import database
        from app.core.config import settings

        capacity = pool_capacity(database.engine)
        if capacity is None:
            return self.configured
        headroom = capacity - settings.public_chat_turn_max_concurrency - 2
        return max(1, min(self.configured, headroom))

    def _sem(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            self.limit = self.effective_limit()
            semaphore = asyncio.Semaphore(self.limit)
            self._semaphores[loop] = semaphore
        return semaphore

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._sem():
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                yield
            finally:
                self.active -= 1
