from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentAuthorization,
    CurrentPrincipal,
    CurrentTenant,
    RequireCapability,
)
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.repositories.ticket_repository import TicketRepository
from app.schemas.ticket import (
    TicketAssignmentUpdate,
    TicketCreate,
    TicketRead,
    TicketUpdate,
)
from app.services.ticket_assignment_service import TicketAssignmentService
from app.services.ticket_service import TicketService

router = APIRouter(
    prefix="/tickets",
    tags=["Tickets"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

TicketReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TICKET_READ)),
]
TicketWriteAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TICKET_WRITE)),
]


@router.post(
    "",
    response_model=TicketRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_ticket(
    data: TicketCreate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketWriteAuthz,
):
    # organization_id comes from CurrentTenant, never from client payload
    try:
        return await TicketService.create_ticket_for_tenant(
            db, data, tenant.organization_id
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found in this organization",
        )
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ticket resource conflict.",
        )


@router.get(
    "",
    response_model=list[TicketRead],
)
async def list_tickets(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketReadAuthz,
    offset: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=100,
    ),
    customer_id: int | None = Query(
        default=None,
        ge=1,
    ),
):
    try:
        return await TicketService.list_tickets_for_tenant(
            db=db,
            organization_id=tenant.organization_id,
            offset=offset,
            limit=limit,
            customer_id=customer_id,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found in this organization",
        )


@router.get(
    "/{ticket_id}",
    response_model=TicketRead,
)
async def get_ticket(
    ticket_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketReadAuthz,
):
    ticket = await TicketService.get_ticket_for_tenant(
        db=db,
        ticket_id=ticket_id,
        organization_id=tenant.organization_id,
    )

    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    return ticket


@router.patch(
    "/{ticket_id}/assignment",
    response_model=TicketRead,
)
async def update_ticket_assignment(
    ticket_id: int,
    data: TicketAssignmentUpdate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketWriteAuthz,
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
        if "service_queue_id" in data.model_dump(exclude_unset=True):
            if data.service_queue_id is not None:
                await TicketAssignmentService.assign_queue(
                    db,
                    ticket=ticket,
                    queue_id=data.service_queue_id,
                    authz=authorization,
                )
            else:
                await TicketAssignmentService.unassign_queue(
                    db,
                    ticket=ticket,
                    authz=authorization,
                )

        if "assigned_subject" in data.model_dump(exclude_unset=True):
            if data.assigned_subject is not None:
                await TicketAssignmentService.assign_subject(
                    db,
                    ticket=ticket,
                    subject=data.assigned_subject,
                    authz=authorization,
                )
            else:
                await TicketAssignmentService.unassign_subject(
                    db,
                    ticket=ticket,
                    authz=authorization,
                )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return ticket


@router.patch(
    "/{ticket_id}",
    response_model=TicketRead,
)
async def update_ticket(
    ticket_id: int,
    data: TicketUpdate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketWriteAuthz,
):
    ticket = await TicketService.update_ticket_for_tenant(
        db=db,
        ticket_id=ticket_id,
        data=data,
        organization_id=tenant.organization_id,
    )

    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    return ticket
