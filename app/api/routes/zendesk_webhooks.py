import json
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.integrations.zendesk.security import (
    verify_zendesk_signature,
)
from app.schemas.webhook import (
    TicketEventWebhook,
    WebhookReceipt,
)
from app.services.webhook_service import WebhookService


router = APIRouter(
    prefix="/webhooks/zendesk",
    tags=["Zendesk Webhooks"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


@router.post(
    "/tickets",
    response_model=WebhookReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_zendesk_ticket_webhook(
    request: Request,
    db: DatabaseSession,
):
    signature = request.headers.get(
        "x-zendesk-webhook-signature"
    )

    timestamp = request.headers.get(
        "x-zendesk-webhook-signature-timestamp"
    )

    invocation_id = request.headers.get(
        "x-zendesk-webhook-invocation-id"
    )

    if not signature or not timestamp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Zendesk webhook signature",
        )

    if not invocation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Zendesk invocation ID",
        )

    raw_body = await request.body()

    valid = verify_zendesk_signature(
        secret=settings.zendesk_webhook_secret,
        timestamp=timestamp,
        body=raw_body,
        signature=signature,
    )

    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Zendesk webhook signature",
        )

    try:
        payload = json.loads(raw_body)

        event_type = str(
            payload["event_type"]
        )

        ticket_id = int(
            payload["ticket_id"]
        )

    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Webhook payload requires valid "
                "event_type and ticket_id"
            ),
        )

    data = TicketEventWebhook(
        event_id=invocation_id,
        event_type=event_type,
        source="zendesk",
        ticket_id=ticket_id,
        payload=payload,
    )

    return await WebhookService.receive_ticket_event(
        db=db,
        data=data,
    )