"""Per-message delivery lease: at most one worker delivers a given reply.

Job claims are fenced, but a worker can still be *running* after its claim was
recovered and re-claimed (it ran past the stale window). Two workers would then
deliver the same reply concurrently: both scan for the reply marker before
either has written it, and both send. The lease closes that window.

It is a PostgreSQL **session-level** advisory lock taken with
``pg_try_advisory_lock`` on a dedicated connection, not a transaction-level
lock: the delivery path commits in the middle (OAuth token refresh, SLA
first-response recording), and a transaction-scoped lock would be released by
those commits. Never blocks: a busy lease means another worker is delivering
right now, which the caller turns into a retryable error.

The lock is released on exit, including cancellation. If the unlock itself
fails, the connection is invalidated (closed at the server), which releases
every lock it held - a leaked lease must never make a reply undeliverable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.core import database
from app.core.logging import get_logger

log = get_logger("delivery_lease")

# High 32 bits of the advisory key per lease kind; low 32 bits: the row id.
REPLY_DELIVERY_LOCK_NAMESPACE = 0x52504C59  # "RPLY": one delivery per reply
CHAT_TURN_LOCK_NAMESPACE = 0x5455524E  # "TURN": one AI turn per chat session


def _lease_key(namespace: int, row_id: int) -> int:
    if not 0 < row_id < 2**31:
        raise ValueError("row id out of range for an advisory lease")
    return (namespace << 32) | row_id


def reply_lease_key(message_id: int) -> int:
    return _lease_key(REPLY_DELIVERY_LOCK_NAMESPACE, message_id)


def chat_turn_lease_key(session_id: int) -> int:
    return _lease_key(CHAT_TURN_LOCK_NAMESPACE, session_id)


@asynccontextmanager
async def reply_delivery_lease(message_id: int) -> AsyncIterator[bool]:
    """Yield ``True`` if this worker now holds the lease for ``message_id``."""
    async with advisory_lease(reply_lease_key(message_id), label=message_id) as held:
        yield held


@asynccontextmanager
async def chat_turn_lease(session_id: int) -> AsyncIterator[bool]:
    """Yield ``True`` if this process may run the next AI turn of a session.

    Serializes turns per public-chat session across every process (API
    in-process dispatcher and the scheduled worker), so replies are produced
    in message order and never concurrently for one conversation.
    """
    async with advisory_lease(chat_turn_lease_key(session_id), label=session_id) as held:
        yield held


@asynccontextmanager
async def advisory_lease(key: int, *, label: int) -> AsyncIterator[bool]:
    """Non-blocking session-level advisory lock on a dedicated connection."""
    conn = await database.engine.connect()
    acquired = False
    # Until the lock call has returned we cannot know whether the server
    # granted it (cancellation or an error can land right after the grant).
    # In that state the connection must NOT go back to the pool - it may hold
    # the session-level lock - so it is invalidated instead.
    state_known = False
    try:
        result = await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        acquired = bool(result.scalar())
        state_known = True
        # End the implicit transaction; the session-level lock survives it.
        await conn.commit()
        yield acquired
    finally:
        try:
            if not state_known:
                await conn.invalidate()
            elif acquired:
                await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                await conn.commit()
        except BaseException:
            log.warning("advisory_lease_unlock_failed", key_label=label)
            await conn.invalidate()
            raise
        finally:
            await conn.close()
