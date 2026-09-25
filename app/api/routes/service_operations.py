from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentAuthorization,
    CurrentPrincipal,
    CurrentTenant,
    RequireCapability,
)
from app.core.database import get_db
from app.core.metrics import record_service_transformation_request
from app.core.rbac import Capability
from app.repositories.service_escalation_repository import (
    ServiceEscalationRepository,
)
from app.repositories.ticket_repository import TicketRepository
from app.schemas.service_escalation import (
    AcknowledgeResponse,
    EscalationFilters,
    EscalationListItem,
    EscalationSummary,
    WorkloadResponse,
)
from app.schemas.service_operations import (
    AIRoutingSuggestionApply,
    OperationsQueueItem,
    OperationsQueueParams,
    OperationsSummary,
)
from app.schemas.service_transformation import ServiceTransformationSummary
from app.services.service_escalation_service import ServiceEscalationService
from app.services.service_kpi_service import ServiceKPIService
from app.services.service_operations_service import ServiceOperationsService
from app.services.service_transformation_service import ServiceTransformationService
from app.services.ticket_assignment_service import TicketAssignmentService
from app.services.workload_service import WorkloadService

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


@router.get(
    "/transformation",
    response_model=ServiceTransformationSummary,
)
async def get_service_transformation(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
    days: Annotated[int, Query(le=90, ge=7)] = 30,
):
    """Service transformation analytics for the tenant over 7/30/90-day
    windows, with a current-vs-previous period comparison, queue/channel
    breakdowns, AI adoption, specialist usage, human workload and value
    realization.
    """
    del principal, authz
    if days not in (7, 30, 90):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="days must be one of 7, 30, 90",
        )

    record_service_transformation_request()
    return await ServiceTransformationService.summary_for_tenant(
        db,
        organization_id=tenant.organization_id,
        days=days,
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
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from None

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
        ) from None

    return {"ticket_id": ticket.id, "service_queue_id": ticket.service_queue_id}


@router.get(
    "/escalations",
    response_model=list[EscalationListItem],
)
async def list_escalations(
    filters: Annotated[EscalationFilters, Depends()],
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
):
    escalations = await ServiceEscalationRepository.list_for_tenant(
        db,
        organization_id=tenant.organization_id,
        status=filters.status,  # type: ignore[arg-type]
        stage=filters.stage,  # type: ignore[arg-type]
        milestone=filters.milestone,  # type: ignore[arg-type]
        queue_id=filters.queue_id,
        assigned_subject=principal.subject if filters.mine else None,
        limit=filters.limit,
        offset=filters.offset,
    )

    ticket_ids = [e.ticket_id for e in escalations]
    ticket_map = await ServiceKPIService._ticket_map_for_tenant(
        db,
        organization_id=tenant.organization_id,
        ticket_ids=ticket_ids,
    )
    queue_map = await ServiceKPIService._queue_map_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )

    result = []
    for escalation in escalations:
        ticket = ticket_map.get(escalation.ticket_id)
        queue = None
        if ticket is not None and ticket.service_queue_id is not None:
            queue = queue_map.get(ticket.service_queue_id)
        result.append(
            EscalationListItem(
                id=escalation.id,
                ticket_id=escalation.ticket_id,
                conversation_id=None,
                subject=ticket.subject if ticket else "",
                priority=ticket.priority if ticket else "",
                status=ticket.status if ticket else "",
                service_queue_id=queue.id if queue else None,
                service_queue_name=queue.name if queue else None,
                assigned_subject=ticket.assigned_subject if ticket else None,
                milestone=escalation.milestone,
                stage=escalation.stage,
                due_at=escalation.due_at,
                triggered_at=escalation.triggered_at,
                acknowledged_at=escalation.acknowledged_at,
                acknowledged_by_subject=escalation.acknowledged_by_subject,
                resolved_at=escalation.resolved_at,
                resolution_reason=escalation.resolution_reason,
                resolution_sla_cycle=escalation.resolution_sla_cycle,
            )
        )

    return result


@router.get(
    "/escalations/summary",
    response_model=EscalationSummary,
)
async def get_escalations_summary(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
):
    return await ServiceEscalationRepository.summary_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )


@router.post(
    "/escalations/{escalation_id}/acknowledge",
    response_model=AcknowledgeResponse,
)
async def acknowledge_escalation(
    escalation_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: WriteAuthz,
    authorization: CurrentAuthorization,
):
    escalation = await ServiceEscalationService.acknowledge_for_tenant(
        db,
        escalation_id=escalation_id,
        organization_id=tenant.organization_id,
        subject=authorization.subject,
        now=datetime.now(UTC),
    )
    if escalation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escalation not found",
        )

    return AcknowledgeResponse(
        id=escalation.id,
        acknowledged_at=escalation.acknowledged_at,
        acknowledged_by_subject=escalation.acknowledged_by_subject,
        status=escalation.status,
    )


@router.get(
    "/workload",
    response_model=WorkloadResponse,
)
async def get_workload(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: ReadAuthz,
    authorization: CurrentAuthorization,
):
    authorization.require(Capability.MEMBER_READ)

    members = await WorkloadService.workload_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )
    return WorkloadResponse(members=members)
