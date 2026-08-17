from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.integrations.zendesk.client import (
    ZendeskAPIError,
    zendesk_client,
)
from app.schemas.zendesk import (
    ZendeskTicketCreate,
    ZendeskTicketUpdate,
)
from app.services.zendesk_oauth_service import (
    ZendeskReauthorizationRequired,
)
from app.schemas.ticket import TicketRead
from app.services.zendesk_sync_service import ZendeskSyncService

router = APIRouter(
    prefix="/zendesk",
    tags=["Zendesk"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


def handle_zendesk_error(
    exc: Exception,
) -> HTTPException:

    if isinstance(
        exc,
        ZendeskReauthorizationRequired,
    ):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "message": str(exc),
                "reauthorize": (
                    "/auth/zendesk/login"
                ),
            },
        )

    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=str(exc),
    )


@router.get("/me")
async def get_zendesk_current_user(
    db: DatabaseSession,
):
    try:
        return await zendesk_client.get_current_user(
            db
        )

    except (
        ZendeskAPIError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.get("/tickets/{ticket_id}")
async def get_zendesk_ticket(
    ticket_id: int,
    db: DatabaseSession,
):
    try:
        return await zendesk_client.get_ticket(
            db=db,
            ticket_id=ticket_id,
        )

    except (
        ZendeskAPIError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.post("/tickets")
async def create_zendesk_ticket(
    data: ZendeskTicketCreate,
    db: DatabaseSession,
):
    try:
        return await zendesk_client.create_ticket(
            db=db,
            subject=data.subject,
            comment=data.comment,
            requester_name=data.requester_name,
            requester_email=(
                str(data.requester_email)
                if data.requester_email
                else None
            ),
            priority=data.priority,
        )

    except (
        ZendeskAPIError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.patch("/tickets/{ticket_id}")
async def update_zendesk_ticket(
    ticket_id: int,
    data: ZendeskTicketUpdate,
    db: DatabaseSession,
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
        )

    except (
        ZendeskAPIError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)


@router.get("/users/{user_id}")
async def get_zendesk_user(
    user_id: int,
    db: DatabaseSession,
):
    try:
        return await zendesk_client.get_user(
            db=db,
            user_id=user_id,
        )

    except (
        ZendeskAPIError,
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
):
    try:
        return await ZendeskSyncService.sync_ticket(
            db=db,
            zendesk_ticket_id=ticket_id,
        )

    except (
        ZendeskAPIError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)

@router.get("/tickets/{ticket_id}/comments")
async def get_zendesk_ticket_comments(
    ticket_id: int,
    db: DatabaseSession,
):
    try:
        return await zendesk_client.get_ticket_comments(
            db=db,
            ticket_id=ticket_id,
        )

    except (
        ZendeskAPIError,
        ZendeskReauthorizationRequired,
    ) as exc:
        raise handle_zendesk_error(exc)