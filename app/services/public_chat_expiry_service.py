"""Reusable expired public-chat session finalization (Phase 1P.7.3).

A public-chat session can pass its ``expires_at`` while still in a live status
(``ai_active`` / ``human_requested`` / ``human_assigned``). The customer
credential is already invalid at that point - the widget rejects it and no new
message can be accepted - but the database lifecycle was never finalized: the
session is dead, yet the linked ticket is still open and the linked conversation
is still open.

This service finalizes exactly those sessions through the EXISTING shared close
lifecycle ``PublicChatResolutionService.close_lifecycle``. It adds no lifecycle
SQL of its own, deletes nothing, runs no AI/RAG/tool, and never creates an
``IntegrationJob`` or ``AgentRun``.

It is reusable by design: the manual CLI (Phase 1P.7.3A) and the scheduled
production worker (Phase 1P.7.3B) both call it. The scheduled entry point is
:meth:`PublicChatExpiryService.finalize_expired_batch`; see its docstring for
the fairness, lock-wait, partial-commit repair, and failure-reporting contract.

Candidate definition (tenant-scoped, expiry alone is sufficient):

    PublicChatSession.organization_id == tenant
    AND PublicChatSession.status IN ("ai_active", "human_requested",
                                     "human_assigned")
    AND PublicChatSession.expires_at <= now

An already-closed session is never a candidate, so this path cannot resurrect,
re-touch, or double-close a terminal session.

Race safety: each confirmed candidate is re-read tenant-scoped under
``SELECT ... FOR UPDATE`` (with ``populate_existing``) immediately before it is
finalized, and BOTH the live status and ``expires_at <= now`` are re-checked
while that row lock is held. The lock is held across the shared lifecycle call,
whose own commit releases it. A candidate whose row disappeared, whose status
changed, or whose expiry was extended is rolled back and skipped, never mutated.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, event, func, literal, select, text, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.public_chat import PublicChatSession
from app.models.ticket import Ticket
from app.repositories.public_chat_repository import PublicChatRepository
from app.services.public_chat_resolution import (
    PublicChatCloseActor,
    PublicChatResolutionService,
)

log = get_logger(__name__)

# A session is only ever finalized on expiry while it is still live. This is the
# exact complement of the model's terminal ``closed`` status, so an already-
# closed session can never be a candidate.
EXPIRED_FINALIZE_STATUSES = (
    "ai_active",
    "human_requested",
    "human_assigned",
)

# Bounded close actor for the shared lifecycle metric/log label. It is a member
# of ``PUBLIC_CHAT_CLOSE_ACTORS`` so it never widens the Prometheus label set.
EXPIRY_CLOSED_BY: PublicChatCloseActor = "expiry"

# Ticket statuses the close lifecycle leaves behind (mirrors the SLA layer).
_RESOLVED_TICKET_STATUSES = ("solved", "closed")

# Hard ceiling for one scheduled batch (Phase 1P.7.3B). The worker's own batch
# size must sit inside it, so a misconfigured caller can never turn a bounded
# housekeeping pass into an unbounded scan.
MAX_EXPIRY_BATCH_LIMIT = 500

# Transaction-scoped PostgreSQL timeouts for the scheduled pass. ``SKIP LOCKED``
# covers the session row; these bound every OTHER wait (the ticket and
# conversation UPDATEs inside the shared lifecycle, discovery queries) so a
# blocked lock acquisition cannot outlive the worker deadline by more than one
# statement. Applied with ``set_config(..., is_local => true)`` at every
# transaction start, so they never leak to a pooled connection.
DEFAULT_EXPIRY_LOCK_TIMEOUT_MS = 2_000
DEFAULT_EXPIRY_STATEMENT_TIMEOUT_MS = 15_000

ExpiryOutcome = str  # "ok" | "partial_failure" | "failed"

# Stateless rotation (see rotation_rank). Each sequence uses a different
# irrational step; 1, sqrt2, sqrt3, sqrt5, sqrt7 are linearly independent over
# the rationals, so the sequences are jointly equidistributed (Weyl) and every
# combination of tenant rank, row rank, and work-class turn recurs, for any
# integer spacing between consecutive runs' rotation indexes.
_TENANT_ROTATION = (math.sqrt(5) - 1) / 2
_ROW_ROTATION = math.sqrt(2) - 1
_CLASS_ROTATION = math.sqrt(3) - 1
_REPAIR_ROTATION = math.sqrt(7) - 2

# FIFO discovery pages per run after the rotation slot. Each page is LIMITed to
# the remaining capacity and excludes tenants already tried, so locked-out or
# empty tenants cannot waste the whole pass; the page count bounds the queries.
_MAX_DISCOVERY_PAGES = 3

# Rotation index granularity: one step per scheduled (*/5) run.
_ROTATION_BUCKET_SECONDS = 300


@dataclass(frozen=True)
class ExpiredSessionCandidate:
    """One expired live session, with the bounded fields needed for a report."""

    session_id: int
    ticket_id: int
    conversation_id: int
    session_status: str
    ticket_status: str
    conversation_status: str
    expires_at: datetime


@dataclass
class ExpirySnapshot:
    """Read-only audit of expired live sessions for one tenant."""

    organization_id: int
    expired_sessions_checked: int
    candidates: list[ExpiredSessionCandidate] = field(default_factory=list)


@dataclass
class ExpiryFinalizeResult:
    """Outcome of finalizing expired live sessions for one tenant."""

    organization_id: int
    candidates_before: int
    finalized: int
    skipped: list[int] = field(default_factory=list)
    candidates_after: int = 0


@dataclass
class ExpiryBatchResult:
    """Outcome of one bounded, cross-tenant scheduled expiry pass.

    Counts, tenant ids, and bounded failure kinds only - never a token, body,
    email, exception message, or credential.

    - ``candidates_attempted`` counts every unit of capacity used (repairs plus
      finalization attempts) and is always ``<= limit``.
    - ``finalized`` counts committed close transitions, INCLUDING ones whose
      core commit succeeded before a later step raised, so a partial tenant
      failure never under-reports work already in the database.
    - ``pending_milestone`` counts those committed-then-failed closes; the
      repair phase of a later run completes them.
    - ``stop_reason`` is why the pass stopped (``complete`` / ``batch_limit`` /
      ``deadline``). ``outcome`` is ``ok`` / ``partial_failure`` / ``failed``.
      ``exit_reason`` equals ``stop_reason`` only when ``outcome == "ok"``;
      otherwise it is the outcome, so a degraded pass can never be reported as
      ``complete``.
    """

    limit: int
    tenants_discovered: int = 0
    tenants_processed: int = 0
    tenants_failed: list[int] = field(default_factory=list)
    failure_kinds: dict[str, int] = field(default_factory=dict)
    candidates_attempted: int = 0
    finalized: int = 0
    pending_milestone: int = 0
    repaired: int = 0
    skipped: int = 0
    stop_reason: str = "complete"

    @property
    def failures(self) -> int:
        return sum(self.failure_kinds.values())

    @property
    def outcome(self) -> ExpiryOutcome:
        if not self.failures:
            return "ok"
        if self.finalized or self.repaired:
            return "partial_failure"
        return "failed"

    @property
    def exit_reason(self) -> str:
        return self.stop_reason if self.outcome == "ok" else self.outcome

    def record_failure(self, organization_id: int, exc: BaseException) -> str:
        kind = failure_kind(exc)
        self.failure_kinds[kind] = self.failure_kinds.get(kind, 0) + 1
        if organization_id not in self.tenants_failed:
            self.tenants_failed.append(organization_id)
        return kind


def failure_kind(exc: BaseException) -> str:
    """A bounded, non-sensitive label for an exception.

    PostgreSQL errors are labelled by SQLSTATE (``55P03`` lock_not_available,
    ``57014`` query_canceled, ...); anything else by its class name. The message
    is never used: driver messages can embed SQL parameters.
    """
    if isinstance(exc, DBAPIError):
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(
            exc.orig, "pgcode", None
        )
        if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isalnum():
            return f"sqlstate_{sqlstate}"
    return type(exc).__name__


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an int")
    if not 1 <= limit <= MAX_EXPIRY_BATCH_LIMIT:
        raise ValueError(
            f"limit must be between 1 and {MAX_EXPIRY_BATCH_LIMIT} (got {limit})"
        )


def _validate_timeout_ms(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive int of milliseconds")


def rotation_rank(index: int, count: int, alpha: float) -> int:
    """Deterministic rank in [0, count) for run index (Weyl sequence).

    frac(index * alpha) with irrational alpha is equidistributed, so
    every rank recurs with bounded gaps (three-distance theorem). Nothing is
    stored: the run's own index drives the rotation.
    """
    if count <= 1:
        return 0
    return min(count - 1, int(math.fmod(index * alpha, 1.0) * count))


def allocate_fair_shares(pending: list[int], limit: int) -> list[int]:
    """Split ``limit`` across tenants round-robin (water-filling).

    ``pending[i]`` is tenant ``i``'s pending candidate count. Every tenant with
    pending work gets at least one unit when ``len(pending) <= limit`` (which
    discovery guarantees), no tenant gets more than it has pending, and unused
    share flows to tenants that still have work. Bounded: at most ``limit``
    increments over at most ``limit`` tenants.
    """
    shares = [0] * len(pending)
    remaining = limit
    while remaining > 0:
        progressed = False
        for index, count in enumerate(pending):
            if remaining == 0:
                break
            if shares[index] < count:
                shares[index] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return shares


@asynccontextmanager
async def bounded_session(
    session_factory: Callable[[], Any],
    *,
    lock_timeout_ms: int,
    statement_timeout_ms: int,
) -> AsyncIterator[AsyncSession]:
    """A session whose every transaction carries LOCAL lock/statement timeouts.

    ``set_config(name, value, true)`` is the parameterized form of ``SET
    LOCAL``: it lasts until the current transaction ends, so the shared
    lifecycle's own commits cannot drop it (the hook re-applies it on the next
    ``BEGIN``) and nothing leaks to the next user of a pooled connection.
    """
    lock_value = f"{lock_timeout_ms}ms"
    statement_value = f"{statement_timeout_ms}ms"

    def _apply(_session: Any, _transaction: Any, connection: Any) -> None:
        connection.execute(
            text(
                "SELECT set_config('lock_timeout', :lock, true), "
                "set_config('statement_timeout', :stmt, true)"
            ),
            {"lock": lock_value, "stmt": statement_value},
        )

    async with session_factory() as db:
        event.listen(db.sync_session, "after_begin", _apply)
        try:
            yield db
        finally:
            event.remove(db.sync_session, "after_begin", _apply)


class PublicChatExpiryService:
    """Tenant-scoped finalization of live public-chat sessions past expiry."""

    @staticmethod
    async def collect_expired_sessions(
        db: AsyncSession,
        *,
        organization_id: int,
        now: datetime | None = None,
    ) -> ExpirySnapshot:
        """Gather the expired live sessions for one tenant. Read-only.

        ``expired_sessions_checked`` is the number of sessions that are both
        live and past ``expires_at`` - every one of them is a candidate, because
        expiry alone is sufficient to finalize (no ticket/conversation drift is
        required). The linked ticket and conversation are joined tenant-safely
        only to report their bounded statuses; the join is not a drift filter.
        """
        moment = now or datetime.now(UTC)
        rows = (
            await db.execute(
                select(PublicChatSession, Ticket, Conversation)
                .select_from(PublicChatSession)
                .join(
                    Ticket,
                    and_(
                        Ticket.id == PublicChatSession.ticket_id,
                        Ticket.organization_id
                        == PublicChatSession.organization_id,
                    ),
                )
                .join(
                    Conversation,
                    and_(
                        Conversation.id == PublicChatSession.conversation_id,
                        Conversation.organization_id
                        == PublicChatSession.organization_id,
                    ),
                )
                .where(
                    PublicChatSession.organization_id == organization_id,
                    PublicChatSession.status.in_(EXPIRED_FINALIZE_STATUSES),
                    PublicChatSession.expires_at <= moment,
                )
                .order_by(PublicChatSession.id)
            )
        ).all()

        candidates = [
            ExpiredSessionCandidate(
                session_id=session.id,
                ticket_id=session.ticket_id,
                conversation_id=session.conversation_id,
                session_status=session.status,
                ticket_status=ticket.status,
                conversation_status=conversation.status,
                expires_at=session.expires_at,
            )
            for session, ticket, conversation in rows
        ]
        return ExpirySnapshot(
            organization_id=organization_id,
            expired_sessions_checked=len(candidates),
            candidates=candidates,
        )

    @classmethod
    async def finalize_expired_sessions(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        now: datetime | None = None,
    ) -> ExpiryFinalizeResult:
        """Finalize every expired live session for one tenant.

        Each candidate is re-read tenant-scoped under ``SELECT ... FOR UPDATE``
        and re-asserted to still be live with ``expires_at <= now`` while the row
        lock is held; a candidate whose row vanished, whose status changed, or
        whose expiry was extended is rolled back (releasing the lock) and
        skipped. The finalize delegates to
        ``PublicChatResolutionService.close_lifecycle`` with
        ``when=session.expires_at`` so ``closed_at`` records the effective
        terminal time, not the later cleanup time. The shared lifecycle owns its
        own commits (releasing the lock), so the loop is intentionally not wrapped
        in one outer transaction.
        """
        moment = now or datetime.now(UTC)
        snapshot = await cls.collect_expired_sessions(
            db,
            organization_id=organization_id,
            now=moment,
        )

        finalized = 0
        skipped: list[int] = []

        for candidate in snapshot.candidates:
            session = await PublicChatRepository.get_session_for_update_for_tenant(
                db,
                candidate.session_id,
                organization_id,
            )
            if (
                session is None
                or session.status not in EXPIRED_FINALIZE_STATUSES
                or session.expires_at > moment
            ):
                # The row left the tenant scope, its status changed after the
                # scan, or its expiry was extended into the future. Release the
                # lock and skip; a later run re-evaluates it.
                await db.rollback()
                skipped.append(candidate.session_id)
                continue

            await PublicChatResolutionService.close_lifecycle(
                db,
                session=session,
                organization_id=organization_id,
                when=session.expires_at,
                closed_by=EXPIRY_CLOSED_BY,
            )
            finalized += 1

        post = await cls.collect_expired_sessions(
            db,
            organization_id=organization_id,
            now=moment,
        )
        log.info(
            "public_chat_expired_sessions_finalized",
            organization_id=organization_id,
            candidates_before=len(snapshot.candidates),
            finalized=finalized,
            skipped=len(skipped),
            candidates_after=len(post.candidates),
        )
        return ExpiryFinalizeResult(
            organization_id=organization_id,
            candidates_before=len(snapshot.candidates),
            finalized=finalized,
            skipped=skipped,
            candidates_after=len(post.candidates),
        )

    # ------------------------------------------------------------------
    # Scheduled pass (Phase 1P.7.3B)
    # ------------------------------------------------------------------

    @staticmethod
    def _live_expired_filter(now: datetime) -> list[Any]:
        return [
            PublicChatSession.status.in_(EXPIRED_FINALIZE_STATUSES),
            PublicChatSession.expires_at <= now,
        ]

    @staticmethod
    def _repair_filter(now: datetime) -> list[Any]:
        # ``closed_at = expires_at`` is the expiry finalizer's signature, so
        # only closes this path performed are repaired here.
        return [
            PublicChatSession.status == "closed",
            PublicChatSession.closed_at == PublicChatSession.expires_at,
            PublicChatSession.expires_at <= now,
            Ticket.status.in_(_RESOLVED_TICKET_STATUSES),
            Ticket.resolved_at.is_(None),
        ]

    @staticmethod
    def _repair_join() -> Any:
        return and_(
            Ticket.id == PublicChatSession.ticket_id,
            Ticket.organization_id == PublicChatSession.organization_id,
        )

    @staticmethod
    async def discover_tenants_with_expired_sessions(
        db: AsyncSession,
        *,
        now: datetime,
        limit: int,
        exclude: Sequence[int] = (),
    ) -> list[tuple[int, int]]:
        """``(organization_id, pending_count)`` for tenants with expired live
        sessions, oldest pending expiry first. Read-only, server-side, bounded.

        ``GROUP BY organization_id ORDER BY MIN(expires_at), organization_id
        LIMIT :limit``, excluding tenants already tried in this run
        (``exclude`` is at most a few batch sizes long). Oldest-first keeps
        healthy tenants draining FIFO; the rotation slot in
        :meth:`finalize_expired_batch` is what guarantees progress when the
        oldest tenants are permanently failing or locked.
        """
        _validate_limit(limit)
        stmt = (
            select(PublicChatSession.organization_id, func.count())
            .where(*PublicChatExpiryService._live_expired_filter(now))
            .group_by(PublicChatSession.organization_id)
            .order_by(
                func.min(PublicChatSession.expires_at),
                PublicChatSession.organization_id,
            )
            .limit(limit)
        )
        if exclude:
            stmt = stmt.where(PublicChatSession.organization_id.not_in(list(exclude)))
        rows = await db.execute(stmt)
        return [(int(org_id), int(count)) for org_id, count in rows.all()]

    @classmethod
    async def _rotation_tenant(
        cls, db: AsyncSession, *, now: datetime, rotation_index: int
    ) -> int | None:
        """The tenant at rotation rank ``rotation_index`` (by id). Bounded."""
        eligible = cls._live_expired_filter(now)
        total = (
            await db.execute(
                select(
                    func.count(func.distinct(PublicChatSession.organization_id))
                ).where(*eligible)
            )
        ).scalar_one()
        if not total:
            return None
        rank = rotation_rank(rotation_index, int(total), _TENANT_ROTATION)
        org_id = (
            await db.execute(
                select(PublicChatSession.organization_id)
                .where(*eligible)
                .group_by(PublicChatSession.organization_id)
                .order_by(PublicChatSession.organization_id)
                .offset(rank)
                .limit(1)
            )
        ).scalar_one_or_none()
        return None if org_id is None else int(org_id)

    @staticmethod
    async def _rotation_pivot(
        db: AsyncSession,
        *,
        filters: list[Any],
        join: Any | None,
        rotation_index: int,
        alpha: float,
    ) -> tuple[datetime, int] | None:
        """``(expires_at, id)`` of the row at rotation rank. Read-only, bounded.

        Used as the keyset START so a permanently failing head row cannot keep
        every later row of the same ordered set waiting: across runs the start
        point visits every rank (see :func:`rotation_rank`).
        """
        count_stmt = select(func.count()).select_from(PublicChatSession)
        key_stmt = select(PublicChatSession.expires_at, PublicChatSession.id)
        if join is not None:
            count_stmt = count_stmt.join(Ticket, join)
            key_stmt = key_stmt.join(Ticket, join)
        total = (await db.execute(count_stmt.where(*filters))).scalar_one()
        if not total:
            return None
        rank = rotation_rank(rotation_index, int(total), alpha)
        row = (
            await db.execute(
                key_stmt.where(*filters)
                .order_by(PublicChatSession.expires_at, PublicChatSession.id)
                .offset(rank)
                .limit(1)
            )
        ).one_or_none()
        return None if row is None else (row[0], int(row[1]))

    @staticmethod
    async def _lock_next(
        db: AsyncSession,
        *,
        filters: list[Any],
        join: Any | None,
        after: tuple[datetime, int] | None,
        inclusive: bool = False,
        before: tuple[datetime, int] | None = None,
    ) -> PublicChatSession | None:
        """Lock-aware selection of ONE row: ``ORDER BY expires_at, id LIMIT 1
        FOR UPDATE OF public_chat_sessions SKIP LOCKED``.

        Selection and locking are the same statement, so a row held by another
        transaction is passed over in SQL and never consumes capacity, and
        PostgreSQL re-checks the WHERE clause against the newest row version
        once the lock is taken. ``after``/``before`` are a keyset window over
        ``(expires_at, id)`` so the caller always moves forward.
        """
        key = tuple_(PublicChatSession.expires_at, PublicChatSession.id)
        stmt = select(PublicChatSession)
        if join is not None:
            stmt = stmt.join(Ticket, join)
        stmt = stmt.where(*filters)
        def _bound(edge: tuple[datetime, int]) -> Any:
            # Bind with the columns' own types (timestamptz, integer).
            return tuple_(
                literal(edge[0], PublicChatSession.expires_at.type),
                literal(edge[1], PublicChatSession.id.type),
            )

        if after is not None:
            lower = _bound(after)
            stmt = stmt.where(key >= lower if inclusive else key > lower)
        if before is not None:
            stmt = stmt.where(key < _bound(before))
        stmt = (
            stmt.order_by(PublicChatSession.expires_at, PublicChatSession.id)
            .limit(1)
            .with_for_update(of=PublicChatSession, skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def _is_committed_expiry_close(
        db: AsyncSession, *, session_id: int, organization_id: int
    ) -> bool:
        """After a failure: did this session's core close already commit?"""
        row = (
            await db.execute(
                select(
                    PublicChatSession.status,
                    PublicChatSession.closed_at,
                    PublicChatSession.expires_at,
                ).where(
                    PublicChatSession.id == session_id,
                    PublicChatSession.organization_id == organization_id,
                )
            )
        ).one_or_none()
        return (
            row is not None
            and row.status == "closed"
            and row.closed_at == row.expires_at
        )

    @classmethod
    async def _run_lifecycle(
        cls,
        db: AsyncSession,
        result: ExpiryBatchResult,
        *,
        session: PublicChatSession,
        now: datetime,
        repair: bool,
    ) -> bool:
        """Run the shared lifecycle for one LOCKED row. Returns False on failure.

        The row is revalidated under the lock; the lifecycle's own commit
        releases it. A failure is rolled back, labelled with a bounded kind,
        and - for a finalize - checked for a core commit that already landed
        so committed work is never under-reported.
        """
        session_id = session.id
        organization_id = session.organization_id
        if repair:
            valid = (
                session.status == "closed"
                and session.closed_at == session.expires_at
            )
        else:
            valid = (
                session.status in EXPIRED_FINALIZE_STATUSES
                and session.expires_at <= now
            )
        if not valid:
            await db.rollback()
            result.skipped += 1
            return True
        try:
            resolution = await PublicChatResolutionService.close_lifecycle(
                db,
                session=session,
                organization_id=organization_id,
                when=session.expires_at,
                closed_by=EXPIRY_CLOSED_BY,
            )
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            kind = result.record_failure(organization_id, exc)
            committed = False
            if not repair:
                try:
                    committed = await cls._is_committed_expiry_close(
                        db, session_id=session_id, organization_id=organization_id
                    )
                finally:
                    await db.rollback()
                if committed:
                    # Core close committed, SLA follow-up failed: it IS
                    # finalized; a later run's repair phase completes it.
                    result.finalized += 1
                    result.pending_milestone += 1
            if repair:
                log.error(
                    "public_chat_expiry_repair_failed",
                    organization_id=organization_id,
                    session_id=session_id,
                    failure_kind=kind,
                )
            else:
                log.error(
                    "public_chat_expiry_candidate_failed",
                    organization_id=organization_id,
                    session_id=session_id,
                    failure_kind=kind,
                    core_committed=committed,
                )
            return False
        if repair:
            if resolution.reconciled:
                result.repaired += 1
        else:
            result.finalized += 1
        return True

    @classmethod
    async def finalize_expired_batch(
        cls,
        session_factory: Callable[[], Any],
        *,
        limit: int,
        now: datetime | None = None,
        should_stop: Callable[[], bool] | None = None,
        lock_timeout_ms: int = DEFAULT_EXPIRY_LOCK_TIMEOUT_MS,
        statement_timeout_ms: int = DEFAULT_EXPIRY_STATEMENT_TIMEOUT_MS,
        rotation_index: int | None = None,
    ) -> ExpiryBatchResult:
        """One bounded, cross-tenant scheduled expiry pass.

        Capacity: every attempted row (finalized, repaired, failed, or
        revalidation-skipped) costs one unit and the total is ``<= limit``.
        Rows locked by another transaction are passed over in SQL
        (``SKIP LOCKED``) and cost nothing.

        1. **Repair** expiry closes whose SLA milestone failed after the core
           commit, through the SAME ``close_lifecycle`` (already-closed branch:
           ``closed_at`` kept, no second close metric). Capacity
           ``max(1, limit // 5)`` when ``limit >= 2``, so live finalization
           always keeps at least one unit. With ``limit == 1`` the single unit
           alternates between the two work classes on a rotation sequence, so
           neither class can be starved by the other.
        2. **Rotation slot**: one row from the tenant at a rotating rank,
           starting at a rotating row rank inside that tenant. This is the
           progress guarantee against permanently failing or locked tenants
           and rows, no matter how many of them sort first.
        3. **FIFO pages**: tenants by oldest pending expiry with a fair share
           each; within a tenant rows go oldest-first via lock-aware keyset
           selection. A tenant stops at its first failure in this run.

        Rotation is stateless: ``rotation_index`` defaults to the run's
        5-minute bucket number (one step per scheduled run).
        :func:`rotation_rank` uses irrational (Weyl) steps, so for ANY run
        cadence every tenant rank, row rank, and work-class turn recurs; at one
        step per run every one of N ranks is visited within about 2N runs.

        Every transaction carries LOCAL ``lock_timeout``/``statement_timeout``
        (:func:`bounded_session`). ``should_stop`` (the worker deadline) is
        checked before every unit of work. Discovery failures propagate.
        """
        _validate_limit(limit)
        _validate_timeout_ms("lock_timeout_ms", lock_timeout_ms)
        _validate_timeout_ms("statement_timeout_ms", statement_timeout_ms)
        moment = now or datetime.now(UTC)
        turn = (
            int(moment.timestamp()) // _ROTATION_BUCKET_SECONDS
            if rotation_index is None
            else rotation_index
        )
        result = ExpiryBatchResult(limit=limit)

        def _stopped() -> bool:
            if should_stop is not None and should_stop():
                result.stop_reason = "deadline"
                return True
            return False

        def _session() -> Any:
            return bounded_session(
                session_factory,
                lock_timeout_ms=lock_timeout_ms,
                statement_timeout_ms=statement_timeout_ms,
            )

        if _stopped():
            return result

        if limit >= 2:
            repair_cap = max(1, limit // 5)
        else:
            repair_cap = (
                1 if rotation_rank(turn, 2, _CLASS_ROTATION) == 0 else 0
            )

        # -- 1. Repair, from a rotating start, wrapping once ------------------
        if repair_cap:
            repair_filters = cls._repair_filter(moment)
            join = cls._repair_join()
            async with _session() as db:
                pivot = await cls._rotation_pivot(
                    db,
                    filters=repair_filters,
                    join=join,
                    rotation_index=turn,
                    alpha=_REPAIR_ROTATION,
                )
                if pivot is not None:
                    used = 0
                    windows: list[tuple[tuple[datetime, int] | None,
                                        tuple[datetime, int] | None]] = [
                        (pivot, None),
                        (None, pivot),
                    ]
                    for lower, upper in windows:
                        last = lower
                        inclusive = lower is not None
                        while used < repair_cap and not _stopped():
                            session = await cls._lock_next(
                                db,
                                filters=repair_filters,
                                join=join,
                                after=last,
                                inclusive=inclusive,
                                before=upper,
                            )
                            if session is None:
                                await db.rollback()
                                break
                            last, inclusive = (session.expires_at, session.id), False
                            used += 1
                            result.candidates_attempted += 1
                            await cls._run_lifecycle(
                                db, result, session=session, now=moment, repair=True
                            )
                        if used >= repair_cap or result.stop_reason == "deadline":
                            break
        if result.stop_reason == "deadline":
            return result

        # -- 2. Rotation slot -------------------------------------------------
        # The rotated tenant is NOT excluded from the FIFO pages (a single
        # busy tenant must still drain at full capacity); only the row already
        # attempted here is never retried in this run.
        tried: list[int] = []
        attempted: set[int] = set()
        live_filters = cls._live_expired_filter(moment)
        if result.candidates_attempted < limit and not _stopped():
            async with _session() as db:
                org_id = await cls._rotation_tenant(
                    db, now=moment, rotation_index=turn
                )
                if org_id is not None:
                    tenant_filters = [
                        PublicChatSession.organization_id == org_id,
                        *live_filters,
                    ]
                    pivot = await cls._rotation_pivot(
                        db,
                        filters=tenant_filters,
                        join=None,
                        rotation_index=turn,
                        alpha=_ROW_ROTATION,
                    )
                    session = None
                    if pivot is not None:
                        session = await cls._lock_next(
                            db,
                            filters=tenant_filters,
                            join=None,
                            after=pivot,
                            inclusive=True,
                        ) or await cls._lock_next(
                            db, filters=tenant_filters, join=None, after=None,
                            before=pivot,
                        )
                    if session is None:
                        await db.rollback()
                    else:
                        attempted.add(session.id)
                        result.candidates_attempted += 1
                        if await cls._run_lifecycle(
                            db, result, session=session, now=moment, repair=False
                        ):
                            result.tenants_processed += 1
        if result.stop_reason == "deadline":
            return result

        # -- 3. FIFO pages ----------------------------------------------------
        for _page in range(_MAX_DISCOVERY_PAGES):
            capacity = limit - result.candidates_attempted
            if capacity <= 0 or _stopped():
                break
            async with _session() as db:
                tenants = await cls.discover_tenants_with_expired_sessions(
                    db, now=moment, limit=capacity, exclude=tried
                )
            if not tenants:
                break
            result.tenants_discovered += len(tenants)
            shares = allocate_fair_shares([count for _org, count in tenants], capacity)
            for (organization_id, _pending), share in zip(tenants, shares, strict=True):
                tried.append(organization_id)
                if share == 0:
                    continue
                if _stopped():
                    break
                await cls._finalize_tenant_share(
                    _session,
                    result,
                    organization_id=organization_id,
                    share=share,
                    now=moment,
                    stopped=_stopped,
                    attempted=attempted,
                )
            if result.stop_reason == "deadline":
                break

        if (
            result.stop_reason == "complete"
            and result.candidates_attempted >= limit
        ):
            result.stop_reason = "batch_limit"
        return result

    @classmethod
    async def _finalize_tenant_share(
        cls,
        open_session: Callable[[], Any],
        result: ExpiryBatchResult,
        *,
        organization_id: int,
        share: int,
        now: datetime,
        stopped: Callable[[], bool],
        attempted: set[int],
    ) -> None:
        """Up to ``share`` rows of one tenant, oldest first, lock-aware.

        The keyset moves past every row attempted, so a failing row is never
        retried within the run, and the tenant backs off (stops) at its first
        failure so a broken tenant costs at most one unit per run here.
        """
        filters = [
            PublicChatSession.organization_id == organization_id,
            *cls._live_expired_filter(now),
        ]
        failed = False
        async with open_session() as db:
            last: tuple[datetime, int] | None = None
            used = 0
            try:
                while used < share:
                    if stopped():
                        break
                    session = await cls._lock_next(
                        db, filters=filters, join=None, after=last
                    )
                    if session is None:
                        await db.rollback()
                        break
                    last = (session.expires_at, session.id)
                    if session.id in attempted:
                        # Already attempted by the rotation slot this run.
                        await db.rollback()
                        continue
                    attempted.add(session.id)
                    used += 1
                    result.candidates_attempted += 1
                    if not await cls._run_lifecycle(
                        db, result, session=session, now=now, repair=False
                    ):
                        failed = True
                        break
            except Exception as exc:  # noqa: BLE001
                # Selection itself failed (e.g. statement timeout).
                await db.rollback()
                failed = True
                kind = result.record_failure(organization_id, exc)
                log.error(
                    "public_chat_expiry_tenant_failed",
                    organization_id=organization_id,
                    failure_kind=kind,
                )
        if not failed:
            result.tenants_processed += 1
