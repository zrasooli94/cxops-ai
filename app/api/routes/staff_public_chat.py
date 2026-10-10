"""Staff public-chat handoff API (Phase 1P.2).

Unlike the widget routes (which are intentionally auth-free), these endpoints
require a control-center principal, resolve the tenant from memberships, and
are capability-gated. Assignment/release never trust a client-supplied tenant:
the session is always resolved by id + the tenant derived from the principal.

Phase 1P.7 adds the read-only live-pilot summary endpoint on the same
capability boundary (TICKET_READ). Like the rest of the surface it derives the
tenant solely from the authenticated principal, and its response carries no
customer message text, tokens, widget keys, or PII. The staff resolve endpoint
(TICKET_WRITE) closes a live session, solving the linked ticket and closing the
linked conversation in a single transaction.
"""

from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.schemas.staff_public_chat import (
    PublicChatPilotSummaryResponse,
    PublicChatWindow,
    StaffBusinessActionsResponse,
    StaffBusinessActionSummary,
    StaffHandoffSessionsResponse,
    StaffHandoffSessionSummary,
)
from app.services.public_chat_staff_service import (
    PublicChatStaffAssignmentError,
    PublicChatStaffService,
    PublicChatStaffSessionNotFoundError,
)
from app.services.public_chat_summary_service import (
    WINDOW_TO_HOURS,
    PublicChatSummaryService,
)

router = APIRouter(
    prefix="/staff/public-chat",
    tags=["Staff Public Chat"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

HandoffReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TICKET_READ)),
]
HandoffWriteAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TICKET_WRITE)),
]


def _staff_error(exc: Exception) -> None:
    if isinstance(exc, PublicChatStaffSessionNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, PublicChatStaffAssignmentError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    raise exc


@router.get(
    "/summary",
    response_model=PublicChatPilotSummaryResponse,
)
async def public_chat_pilot_summary(
    db: DatabaseSession,
    tenant: CurrentTenant,
    _authz: HandoffReadAuthz,
    window: str = Query(default="24h", pattern="^(24h|7d)$"),
):
    """Read-only live-pilot health summary for the principal's tenant.

    The tenant is always the resolved ``CurrentTenant``; a client-supplied
    organization id is never accepted. The ``window`` is a closed set
    (``24h`` / ``7d``); anything else is rejected by request validation. The
    response is bounded aggregates only — no customer message text, tokens,
    widget keys, or PII.
    """
    result = await PublicChatSummaryService.summary(
        db,
        organization_id=tenant.organization_id,
        window_hours=WINDOW_TO_HOURS[window],
    )
    return PublicChatPilotSummaryResponse(
        tenant_id=tenant.organization_id,
        window=cast(PublicChatWindow, window),
        generated_at=datetime.now(UTC),
        **result,
    )


@router.get(
    "/handoff",
    response_model=StaffHandoffSessionsResponse,
)
async def list_handoff_sessions(
    db: DatabaseSession,
    tenant: CurrentTenant,
    _authz: HandoffReadAuthz,
):
    """Sessions awaiting (or in) staff handoff for this tenant."""
    sessions = await PublicChatStaffService.list_handoff_sessions(
        db,
        organization_id=tenant.organization_id,
    )
    return StaffHandoffSessionsResponse(
        sessions=[StaffHandoffSessionSummary(**session) for session in sessions]
    )


@router.post(
    "/sessions/{session_id}/assign",
    response_model=StaffHandoffSessionSummary,
)
async def assign_session(
    session_id: int,
    db: DatabaseSession,
    tenant: CurrentTenant,
    principal: CurrentPrincipal,
    _authz: HandoffWriteAuthz,
):
    """Assign a human-requested session to the calling staff member."""
    try:
        result = await PublicChatStaffService.assign(
            db,
            session_id=session_id,
            organization_id=tenant.organization_id,
            subject=principal.subject,
        )
    except (
        PublicChatStaffSessionNotFoundError,
        PublicChatStaffAssignmentError,
    ) as exc:
        _staff_error(exc)
    return StaffHandoffSessionSummary(**result)


@router.post(
    "/sessions/{session_id}/release",
    response_model=StaffHandoffSessionSummary,
)
async def release_session(
    session_id: int,
    db: DatabaseSession,
    tenant: CurrentTenant,
    _authz: HandoffWriteAuthz,
):
    """Return an assigned session to the handoff queue."""
    try:
        result = await PublicChatStaffService.release(
            db,
            session_id=session_id,
            organization_id=tenant.organization_id,
        )
    except (
        PublicChatStaffSessionNotFoundError,
        PublicChatStaffAssignmentError,
    ) as exc:
        _staff_error(exc)
    return StaffHandoffSessionSummary(**result)


@router.post(
    "/sessions/{session_id}/resolve",
    response_model=StaffHandoffSessionSummary,
)
async def resolve_session(
    session_id: int,
    db: DatabaseSession,
    tenant: CurrentTenant,
    _authz: HandoffWriteAuthz,
):
    """Close a live handoff session, solving its ticket and conversation.

    Resolving an already-closed session is an idempotent reconciliation: it
    repairs a drifted linked ticket/conversation and a missing SLA milestone,
    then returns the stable closed summary. Nothing is deleted: messages, the
    ticket, the conversation, agent runs, audit trail, and RAG logs are all
    preserved.
    """
    try:
        result = await PublicChatStaffService.resolve(
            db,
            session_id=session_id,
            organization_id=tenant.organization_id,
        )
    except (
        PublicChatStaffSessionNotFoundError,
        PublicChatStaffAssignmentError,
    ) as exc:
        _staff_error(exc)
    return StaffHandoffSessionSummary(**result)


@router.get(
    "/sessions/{session_id}/business-actions",
    response_model=StaffBusinessActionsResponse,
)
async def list_session_business_actions(
    session_id: int,
    db: DatabaseSession,
    tenant: CurrentTenant,
    _authz: HandoffReadAuthz,
):
    """Customer-visible business actions executed for a handoff session."""
    session = await PublicChatStaffService.resolve_session(
        db,
        session_id=session_id,
        organization_id=tenant.organization_id,
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )
    actions = await PublicChatStaffService.list_business_actions(
        db,
        organization_id=tenant.organization_id,
        conversation_id=session.conversation_id,
    )
    return StaffBusinessActionsResponse(
        actions=[StaffBusinessActionSummary(**action) for action in actions]
    )