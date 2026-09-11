from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import verify_ticket_event_signature
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
    raw_body: Annotated[bytes, Depends(verify_ticket_event_signature)],
    db: DatabaseSession,
):
    try:
        data = TicketEventWebhook.model_validate_json(raw_body)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid webhook payload",
        )

    return await WebhookService.receive_ticket_event(
        db=db,
        data=data,
    )
