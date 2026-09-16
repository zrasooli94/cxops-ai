from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.integrations.zendesk.client import (
    ZendeskAPIError,
    zendesk_client,
)
from app.schemas.ticket import TicketRead
from app.schemas.zendesk import (
    ZendeskTicketCreate,
    ZendeskTicketUpdate,
)
from app.services.zendesk_oauth_service import (
    ZendeskNotConfiguredError,
    ZendeskReauthorizationRequired,
)
from app.services.zendesk_sync_service import (
    ZendeskSyncConflictError,
    ZendeskSyncService,
)

router = APIRouter(
    prefix="/zendesk",
    tags=["Zendesk"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

CustomerReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.CUSTOMER_READ)),
]
IntegrationReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.INTEGRATION_READ)),
]
TicketReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TICKET_READ)),
]
TicketWriteAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TICKET_WRITE)),
]


def handle_zendesk_error(
    exc: Exception,
) -> HTTPException:
    """Map Zendesk errors to controlled HTTP responses.

    ``ZendeskNotConfiguredError`` intentionally does not reveal whether *other*
    organizations have a connection; it only reports this organization's own
    integration status.
    """
    if isinstance(
        exc,
        ZendeskNotConfiguredError,
    ):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Zendesk integration is not configured.",
        )

    if isinstance(
        exc,
        ZendeskReauthorizationRequired,
    ):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "message": "Zendesk integration reauthorization required.",
                "reauthorize": "/auth/zendesk/login",
            },
        )

    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Zendesk request failed.",
    )


@router.get("/me")
async def get_zendesk_current_user(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: IntegrationReadAuthz,
):
    try:
        return await zendesk_client.get_current_user(
            db=db,
            organization_id=tenant.organization_id,
        )

    except (
        ZendeskAPIError,
        ZendeskNotConfiguredError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.get("/tickets/{ticket_id}")
async def get_zendesk_ticket(
    ticket_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketReadAuthz,
):
    try:
        return await zendesk_client.get_ticket(
            db=db,
            ticket_id=ticket_id,
            organization_id=tenant.organization_id,
        )

    except (
        ZendeskAPIError,
        ZendeskNotConfiguredError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.post("/tickets")
async def create_zendesk_ticket(
    data: ZendeskTicketCreate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketWriteAuthz,
):
    try:
        return await zendesk_client.create_ticket(
            db=db,
            organization_id=tenant.organization_id,
            subject=data.subject,
            comment=data.comment,
            requester_name=data.requester_name,
            requester_email=(
                str(data.requester_email) if data.requester_email else None
            ),
            priority=data.priority,
        )

    except (
        ZendeskAPIError,
        ZendeskNotConfiguredError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.patch("/tickets/{ticket_id}")
async def update_zendesk_ticket(
    ticket_id: int,
    data: ZendeskTicketUpdate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketWriteAuthz,
):
    changes = data.model_dump(
        exclude_none=True,
    )

    if "comment" in changes:
        changes["comment"] = {
            "body": changes["comment"],
        }

    try:
        return await zendesk_client.update_ticket(
            db=db,
            ticket_id=ticket_id,
            changes=changes,
            organization_id=tenant.organization_id,
        )

    except (
        ZendeskAPIError,
        ZendeskNotConfiguredError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.get("/users/{user_id}")
async def get_zendesk_user(
    user_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: CustomerReadAuthz,
):
    try:
        return await zendesk_client.get_user(
            db=db,
            user_id=user_id,
            organization_id=tenant.organization_id,
        )

    except (
        ZendeskAPIError,
        ZendeskNotConfiguredError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.post(
    "/tickets/{ticket_id}/sync",
    response_model=TicketRead,
)
async def sync_zendesk_ticket(
    ticket_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketWriteAuthz,
):
    try:
        return await ZendeskSyncService.sync_ticket_for_tenant(
            db=db,
            zendesk_ticket_id=ticket_id,
            organization_id=tenant.organization_id,
        )

    except ZendeskSyncConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Zendesk ticket is already linked to an existing record.",
        )

    except (
        ZendeskAPIError,
        ZendeskNotConfiguredError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.get("/tickets/{ticket_id}/comments")
async def get_zendesk_ticket_comments(
    ticket_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketReadAuthz,
):
    try:
        return await zendesk_client.get_ticket_comments(
            db=db,
            ticket_id=ticket_id,
            organization_id=tenant.organization_id,
        )

    except (
        ZendeskAPIError,
        ZendeskNotConfiguredError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)
