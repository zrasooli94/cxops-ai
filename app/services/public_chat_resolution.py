"""Shared public-chat resolution lifecycle (Phase 1P.7.1).

Both the staff Resolve action and the customer widget "End conversation" run
the same close lifecycle: the session moves to ``closed`` (``closed_at``
recorded), the linked ticket to ``solved``, and the linked conversation to
``closed`` in one atomic, tenant-scoped transaction via
``PublicChatRepository.resolve_session_for_tenant``, then the SLA layer
re-records the resolution milestone. Nothing is deleted; conversation history
is preserved.

Retry semantics are part of the contract:

- an already-closed session is an idempotent success, so a retried request
  converges instead of surfacing as a conflict;
- an already-closed session also reconciles its core links (linked ticket ->
  ``solved``, linked conversation -> ``closed``) atomically when legacy or
  reopened drift left them open, and then re-records the SLA milestone. The
  session row is never touched, so ``closed_at`` and the assignment trail are
  preserved, and this repair never counts as a new close transition;
- the close metric and log are emitted only when this request actually
  performed the non-closed -> closed transition, so a retry never double-fires
  the audit signal. They are emitted immediately after the atomic core commit
  succeeds and BEFORE the SLA follow-up: if that follow-up fails the request may
  error, but the close is still audited exactly once, and a retried close
  repairs SLA drift without re-emitting;
- the SLA milestone lives in a transaction *after* the atomic core commit. If
  that follow-up failed, the session is already closed and the ticket already
  ``solved`` but still has ``resolved_at IS NULL``; a retried close detects
  that and repairs it rather than treating "already closed" as an unconditional
  no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import record_public_chat_session_closed
from app.models.public_chat import PublicChatSession
from app.repositories.public_chat_repository import PublicChatRepository
from app.repositories.ticket_repository import TicketRepository
from app.services.ticket_sla_service import (
    RESOLVED_TICKET_STATUSES,
    TicketSLAService,
)

log = get_logger(__name__)


@dataclass(frozen=True)
class PublicChatResolution:
    """Outcome of running the shared close lifecycle for one session."""

    session: PublicChatSession
    transitioned: bool
    reconciled: bool


class PublicChatResolutionService:
    """The one close lifecycle shared by staff Resolve and the widget.

    The caller resolves the session (staff by id + tenant, widget by token) and
    passes it in; this service owns the atomic transition, the SLA milestone
    record, and the once-per-transition audit signal.
    """

    @classmethod
    async def close_lifecycle(
        cls,
        db: AsyncSession,
        *,
        session: PublicChatSession,
        organization_id: int,
        when: datetime,
        closed_by: str,
    ) -> PublicChatResolution:
        """Close a tenant-scoped session plus its ticket and conversation.

        ``closed_by`` is the bounded actor label for the metric and the log
        (``"staff"`` or ``"customer"``) — never a principal subject or any
        customer value.
        """
        transitioned = False
        if session.status != "closed":
            resolved = await PublicChatRepository.resolve_session_for_tenant(
                db,
                session_id=session.id,
                organization_id=organization_id,
                when=when,
            )
            if resolved is not None:
                session = resolved
                transitioned = True
            else:
                # Lost the race to a concurrent close: the conditional UPDATE
                # matched nothing, so the session became "closed" between the
                # caller's lookup and the UPDATE. Re-read it so reconciliation
                # runs against current state, and treat this request as an
                # idempotent retry rather than a transition (the winner owns
                # the audit signal).
                re_read = await PublicChatRepository.get_session_by_id_for_tenant(
                    db,
                    session.id,
                    organization_id,
                )
                if re_read is not None:
                    session = re_read

        if transitioned:
            # The atomic core commit succeeded, so the close audit signal is
            # emitted immediately — BEFORE the SLA follow-up. If that follow-up
            # fails, the first request can error, but the close is already
            # audited exactly once, and a retried close repairs the SLA without
            # double-firing. Never emit before the core transaction commits:
            # the metric/log must not claim a close that did not happen.
            log.info(
                "public_chat_session_closed",
                session_id=session.id,
                organization_id=organization_id,
                ticket_id=session.ticket_id,
                closed_by=closed_by,
            )
            record_public_chat_session_closed(closed_by=closed_by)
        else:
            # Already closed — this request was a retry, or a concurrent close
            # won the race. A closed session can still carry legacy/reopened
            # drift (open linked ticket/conversation), so reconcile its core
            # links here. The session row is never touched, so ``closed_at``
            # and the assignment trail are preserved, and a legacy repair does
            # NOT count as a new close transition (no metric, no log).
            repaired = (
                await PublicChatRepository.reconcile_closed_session_links_for_tenant(
                    db,
                    session_id=session.id,
                    organization_id=organization_id,
                )
            )
            if repaired is not None:
                session = repaired

        reconciled = await cls._reconcile_resolution_milestone(
            db,
            session=session,
            organization_id=organization_id,
        )

        return PublicChatResolution(
            session=session,
            transitioned=transitioned,
            reconciled=reconciled,
        )

    @staticmethod
    async def _reconcile_resolution_milestone(
        db: AsyncSession,
        *,
        session: PublicChatSession,
        organization_id: int,
    ) -> bool:
        """Re-run the post-close SLA milestone when a prior attempt left it unset.

        ``resolve_session_for_tenant`` deliberately marks the ticket ``solved``
        without touching ``resolved_at``; the SLA layer records that milestone
        in the transaction that follows the atomic core commit. If that
        follow-up never ran (or failed after the core commit), the session is
        already closed and the ticket already solved but ``resolved_at`` is
        still NULL. Re-recording here repairs the milestone so a retried close
        converges. ``TicketSLAService.record_resolution`` is itself idempotent
        (it early-returns once ``resolved_at`` is set), so this is safe to run
        on every close request.
        """
        ticket = await TicketRepository.get_by_id_for_tenant(
            db,
            session.ticket_id,
            organization_id,
        )
        if ticket is None:
            return False
        if (
            ticket.resolved_at is not None
            or ticket.status not in RESOLVED_TICKET_STATUSES
        ):
            return False
        await TicketSLAService.record_resolution(db, ticket)
        await db.commit()
        return True