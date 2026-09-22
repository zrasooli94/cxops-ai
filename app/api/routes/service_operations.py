from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentAuthorization,
    CurrentPrincipal,
    CurrentTenant,
    RequireCapability,
)
from app.core.database import get_db
from app.core.rbac import Capability
from app.repositories.ticket_repository import TicketRepository
from app.schemas.service_operations import (
    AIRoutingSuggestionApply,
    OperationsQueueItem,
    OperationsQueueParams,
    OperationsSummary,
)
from app.services.service_operations_service import ServiceOperationsService
from app.services.ticket_assignment_service import TicketAssignmentService

router = APIRouter(
    prefix="/service-operations",
    tags=["Service Operations"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]
ReadAuthz = Annotated[
    Capability,
    Depends(RequireCapability(Capability.TICKET_READ)),
]
WriteAuthz = Annotated[
    Capability,
    Depends(RequireCapability(Capability.TICKET_WRITE)),
]


@router.get(
    "/queue",
    response_model=list[OperationsQueueItem],
)
async def get_operations_queue(
    params: Annotated[OperationsQueueParams, Depends()],
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
):
    assignee_subject = None
    if params.mine:
        assignee_subject = principal.subject

    return await ServiceOperationsService.list_queue_for_tenant(
        db,
        organization_id=tenant.organization_id,
        queue_id=params.queue_id,
        status=params.status,
        priority=params.priority,
        sla_state=params.sla_state,
        needs_response=params.needs_response,
        unassigned=params.unassigned,
        assignee_subject=assignee_subject,
        search=params.search,
        limit=params.limit,
        offset=params.offset,
    )


@router.get(
    "/summary",
    response_model=OperationsSummary,
)
async def get_operations_summary(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
):
    return await ServiceOperationsService.summary_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )


@router.post(
    "/tickets/{ticket_id}/claim",
    response_model=dict,
)
async def claim_ticket(
    ticket_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: WriteAuthz,
    authorization: CurrentAuthorization,
):
    ticket = await TicketRepository.get_by_id_for_tenant(
        db,
        ticket_id=ticket_id,
        organization_id=tenant.organization_id,
    )
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    try:
        await TicketAssignmentService.claim_ticket(
            db,
            ticket=ticket,
            authz=authorization,
        )
    except ValueError as exc:
        if "already claimed" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return {"ticket_id": ticket.id, "assigned_subject": ticket.assigned_subject}


@router.post(
    "/tickets/{ticket_id}/apply-suggestion",
    response_model=dict,
)
async def apply_ai_suggestion(
    ticket_id: int,
    data: AIRoutingSuggestionApply,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: WriteAuthz,
    authorization: CurrentAuthorization,
):
    ticket = await TicketRepository.get_by_id_for_tenant(
        db,
        ticket_id=ticket_id,
        organization_id=tenant.organization_id,
    )
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    try:
        await TicketAssignmentService.assign_queue(
            db,
            ticket=ticket,
            queue_id=data.queue_id,
            authz=authorization,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return {"ticket_id": ticket.id, "service_queue_id": ticket.service_queue_id}
