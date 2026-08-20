from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.webhook import (
    TicketEventWebhook,
    WebhookReceipt,
)
from app.services.webhook_service import WebhookService

router = APIRouter(
    prefix="/webhooks",
    tags=["Webhooks"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


@router.post(
    "/ticket-events",
    response_model=WebhookReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_ticket_event(
    data: TicketEventWebhook,
    db: DatabaseSession,
):
    return await WebhookService.receive_ticket_event(
        db=db,
        data=data,
    )
