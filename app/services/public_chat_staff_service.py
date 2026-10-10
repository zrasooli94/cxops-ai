"""Staff-facing public-chat handoff orchestration (Phase 1P.2/1P.7).

The widget routes stay auth-free; these staff operations live on authenticated,
tenant-scoped endpoints guarded by ``RequireCapability``. Assignment moves a
``human_requested`` session to ``human_assigned`` (recording the principal
subject), release returns it to the queue, resolve closes a live session and
its linked ticket + conversation in one atomic transition. Every lookup is
scoped by the tenant resolved from the authenticated principal — never from a
client id.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import (
    record_public_chat_handoff,
)
from app.models.public_chat import PublicChatSession
from app.repositories.business_action_repository import BusinessActionRepository
from app.repositories.public_chat_repository import PublicChatRepository
from app.services.public_chat_resolution import PublicChatResolutionService

log = get_logger(__name__)

RESOLVABLE_STATUSES: tuple[str, ...] = (
    "ai_active",
    "human_requested",
    "human_assigned",
)


class PublicChatStaffError(Exception):
    pass


class PublicChatStaffSessionNotFoundError(PublicChatStaffError):
    pass


class PublicChatStaffAssignmentError(PublicChatStaffError):
    pass


class PublicChatStaffService:
    @staticmethod
    async def resolve_session(
        db: AsyncSession,
        *,
        session_id: int,
        organization_id: int,
    ) -> PublicChatSession | None:
        return await PublicChatRepository.get_session_by_id_for_tenant(
            db,
            session_id,
            organization_id,
        )

    @staticmethod
    def _session_summary(session: PublicChatSession) -> dict:
        return {
            "session_id": session.id,
            "conversation_id": session.conversation_id,
            "ticket_id": session.ticket_id,
            "customer_id": session.customer_id,
            "status": session.status,
            "assigned_to_subject": session.assigned_to_subject,
            "assigned_at": session.assigned_at,
            "released_at": session.released_at,
            "expires_at": session.expires_at,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    @classmethod
    async def list_handoff_sessions(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        limit: int = 100,
    ) -> list[dict]:
        sessions = await PublicChatRepository.list_handoff_sessions_for_tenant(
            db,
            organization_id,
            limit=limit,
        )
        return [cls._session_summary(session) for session in sessions]

    @classmethod
    async def assign(
        cls,
        db: AsyncSession,
        *,
        session_id: int,
        organization_id: int,
        subject: str,
    ) -> dict:
        if not subject:
            raise PublicChatStaffAssignmentError(
                "Assignment requires a principal subject."
            )
        # Resolve first only to separate "no such session in this tenant"
        # (404) from "wrong state" (409). It is deliberately NOT the authority
        # on whether the transition is legal: the atomic conditional UPDATE is,
        # so a lost race lands on the 409 branch below instead of silently
        # double-assigning.
        session = await PublicChatRepository.get_session_by_id_for_tenant(
            db,
            session_id,
            organization_id,
        )
        if session is None:
            raise PublicChatStaffSessionNotFoundError("Session not found.")
        if session.status != "human_requested":
            raise PublicChatStaffAssignmentError(
                "Only a human-requested session can be assigned."
            )
        assigned = await PublicChatRepository.assign_session_for_tenant(
            db,
            session_id=session_id,
            organization_id=organization_id,
            subject=subject,
            when=datetime.now(UTC),
        )
        if assigned is None:
            # The status changed between the resolve and the conditional
            # UPDATE (a concurrent assign/release won the race).
            raise PublicChatStaffAssignmentError(
                "Only a human-requested session can be assigned."
            )
        # subject is the IdP's pseudonymous end-user identifier, not a name,
        # email, or message. It is kept deliberately: an assignment is a staff
        # action on one specific customer conversation, and without it an
        # incident cannot be tied back to the person who reported it. Nothing
        # that identifies the customer in the clear is logged.
        log.info(
            "public_chat_session_assigned",
            session_id=session_id,
            organization_id=organization_id,
            subject=subject,
        )
        record_public_chat_handoff(outcome="assigned")
        return cls._session_summary(assigned)

    @classmethod
    async def release(
        cls,
        db: AsyncSession,
        *,
        session_id: int,
        organization_id: int,
    ) -> dict:
        session = await PublicChatRepository.get_session_by_id_for_tenant(
            db,
            session_id,
            organization_id,
        )
        if session is None:
            raise PublicChatStaffSessionNotFoundError("Session not found.")
        if session.status != "human_assigned":
            raise PublicChatStaffAssignmentError(
                "Only an assigned session can be released."
            )
        released = await PublicChatRepository.release_session_for_tenant(
            db,
            session_id=session_id,
            organization_id=organization_id,
            when=datetime.now(UTC),
        )
        if released is None:
            # A concurrent release already returned it to the queue.
            raise PublicChatStaffAssignmentError(
                "Only an assigned session can be released."
            )
        log.info(
            "public_chat_session_released",
            session_id=session_id,
            organization_id=organization_id,
        )
        record_public_chat_handoff(outcome="released")
        return cls._session_summary(released)

    @classmethod
    async def resolve(
        cls,
        db: AsyncSession,
        *,
        session_id: int,
        organization_id: int,
    ) -> dict:
        """Close a handoff session and its linked ticket + conversation.

        The transition is the shared public-chat resolution lifecycle: the
        session moves to ``closed`` (``closed_at`` recorded), the ticket to
        ``solved``, and the conversation to ``closed`` in one atomic,
        tenant-scoped transaction, then the SLA layer re-records the resolution
        milestone. The assignment trail (``assigned_to_subject``/``assigned_at``)
        is preserved for audit. The close metric and log are emitted only when
        this request actually performs the transition.

        Resolving an already-closed session is an idempotent reconciliation
        (not an unconditional no-op): it repairs legacy/reopened drift in the
        linked ticket/conversation and a missing SLA milestone if a previous
        attempt failed, so a retried resolve converges and never double-fires
        side effects.
        """
        session = await PublicChatRepository.get_session_by_id_for_tenant(
            db,
            session_id,
            organization_id,
        )
        if session is None:
            raise PublicChatStaffSessionNotFoundError("Session not found.")
        if session.status != "closed" and session.status not in RESOLVABLE_STATUSES:
            raise PublicChatStaffAssignmentError(
                "Only an active session can be resolved."
            )
        result = await PublicChatResolutionService.close_lifecycle(
            db,
            session=session,
            organization_id=organization_id,
            when=datetime.now(UTC),
            closed_by="staff",
        )
        return cls._session_summary(result.session)

    @staticmethod
    async def list_business_actions(
        db: AsyncSession,
        *,
        organization_id: int,
        conversation_id: int,
        limit: int = 50,
    ) -> list[dict]:
        actions = await BusinessActionRepository.list_for_conversation_for_tenant(
            db,
            conversation_id=conversation_id,
            organization_id=organization_id,
            limit=limit,
        )
        summaries = []
        for action in actions:
            result = action.result_json or {}
            summaries.append(
                {
                    "id": action.id,
                    "request_type": action.request_type,
                    "status": action.status,
                    "reference_id": action.reference_id,
                    "summary": result.get("summary"),
                    "created_at": action.created_at,
                    "updated_at": action.updated_at,
                }
            )
        return summaries


public_chat_staff_service = PublicChatStaffService()