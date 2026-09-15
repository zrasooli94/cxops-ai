from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant
from app.core.database import get_db
from app.schemas.ticket import (
    TicketCreate,
    TicketRead,
    TicketUpdate,
)
from app.services.ticket_service import TicketService

router = APIRouter(
    prefix="/tickets",
    tags=["Tickets"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
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
):
    # organization_id comes from CurrentTenant, never from client payload
    try:
        return await TicketService.create_ticket_for_tenant(db, data, tenant.organization_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found in this organization",
        )


@router.get(
    "",
    response_model=list[TicketRead],
)
async def list_tickets(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    offset: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=100,
    ),
):
    return await TicketService.list_tickets_for_tenant(
        db=db,
        organization_id=tenant.organization_id,
        offset=offset,
        limit=limit,
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
    "/{ticket_id}",
    response_model=TicketRead,
)
async def update_ticket(
    ticket_id: int,
    data: TicketUpdate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
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
