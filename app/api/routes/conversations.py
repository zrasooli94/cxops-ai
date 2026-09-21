from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentPrincipal,
    CurrentTenant,
    RequireCapability,
)
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.schemas.conversation import (
    ConversationDetail,
    ConversationListResponse,
    ConversationMessageRead,
    ConversationReplyRequest,
    ConversationReplyResponse,
    ConversationSummaryResponse,
)
from app.services.conversation_reply_service import (
    ConversationReplyError,
    ConversationReplyService,
)
from app.services.inbox_service import InboxService

router = APIRouter(
    prefix="/conversations",
    tags=["Inbox"],
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


@router.get(
    "",
    response_model=ConversationListResponse,
)
async def list_conversations(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketReadAuthz,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    provider: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    customer_id: int | None = Query(default=None, ge=1),
    ticket_id: int | None = Query(default=None, ge=1),
    search: str | None = Query(default=None),
):
    items, total = await InboxService.list_for_tenant(
        db,
        organization_id=tenant.organization_id,
        offset=offset,
        limit=limit,
        status=status_filter,
        provider=provider,
        channel=channel,
        customer_id=customer_id,
        ticket_id=ticket_id,
        search=search,
    )
    return ConversationListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/summary",
    response_model=ConversationSummaryResponse,
)
async def get_conversation_summary(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketReadAuthz,
):
    return await InboxService.summary_for_tenant(
        db,
        organization_id=tenant.organization_id,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
)
async def get_conversation(
    conversation_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketReadAuthz,
):
    detail = await InboxService.get_for_tenant(
        db,
        conversation_id=conversation_id,
        organization_id=tenant.organization_id,
    )

    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    return detail


@router.get(
    "/{conversation_id}/messages",
    response_model=list[ConversationMessageRead],
)
async def get_conversation_messages(
    conversation_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketReadAuthz,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    messages = await InboxService.messages_for_tenant(
        db,
        conversation_id=conversation_id,
        organization_id=tenant.organization_id,
        offset=offset,
        limit=limit,
    )

    if messages is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    return messages


@router.post(
    "/{conversation_id}/replies",
    response_model=ConversationReplyResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_conversation_reply(
    conversation_id: int,
    request: ConversationReplyRequest,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketWriteAuthz,
):
    try:
        result = await ConversationReplyService.enqueue_reply(
            db=db,
            conversation_id=conversation_id,
            body=request.body,
            client_request_id=request.client_request_id,
            organization_id=tenant.organization_id,
            requested_by_subject=principal.subject,
            authz=authz,
        )
    except ConversationReplyError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.args[0],
        ) from exc

    return ConversationReplyResponse(**result)


@router.post(
    "/{conversation_id}/messages/{message_id}/retry",
    response_model=ConversationReplyResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_conversation_message(
    conversation_id: int,
    message_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: TicketWriteAuthz,
):
    try:
        result = await ConversationReplyService.retry_failed_reply(
            db=db,
            conversation_id=conversation_id,
            message_id=message_id,
            organization_id=tenant.organization_id,
            authz=authz,
        )
    except ConversationReplyError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.args[0],
        ) from exc

    return ConversationReplyResponse(**result)