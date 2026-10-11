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
production worker (Phase 1P.7.3B) both call it.

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

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, select
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
