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
from app.schemas.webhook import WebhookReceipt

from app.services.zendesk_webhook_service import (
    ZendeskWebhookService,
)

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
    
        raw_event_type = str(
            payload["type"]
        )
    
        ticket_id = int(
            payload["detail"]["id"]
        )
    
        event_type = raw_event_type.removeprefix(
            "zen:event-type:"
        )
    
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid Zendesk event payload",
        )

    return await ZendeskWebhookService.process(
        db=db,
        invocation_id=invocation_id,
        event_type=event_type,
        zendesk_ticket_id=ticket_id,
        payload=payload,
    )
