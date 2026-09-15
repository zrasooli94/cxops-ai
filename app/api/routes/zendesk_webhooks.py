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

from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.integrations.zendesk.security import (
    verify_zendesk_signature,
)
from app.repositories.zendesk_oauth_token_repository import (
    ZendeskOAuthTokenRepository,
)
from app.schemas.job import JobAccepted
from app.services.integration_job_service import (
    IntegrationJobService,
)

router = APIRouter(
    prefix="/webhooks/zendesk",
    tags=["Zendesk Webhooks"],
)

log = get_logger(__name__)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


@router.post(
    "/tickets/{integration_id}",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_zendesk_ticket_webhook(
    integration_id: str,
    request: Request,
    db: DatabaseSession,
):
    """Machine-to-machine Zendesk webhook, resolved to exactly one organization.

    Trusted identity pipeline:
        integration_id (configured webhook path)
        → organization-owned connection row
        → per-organization webhook secret
        → HMAC verification + bounded replay window
        → exactly one organization_id, then tenant-scoped event processing.

    Fail-closed semantics:
        - unknown integration_id → 404 (never reveals whether an integration
          exists or which org owns one)
        - connection with no webhook secret configured → 401
        - invalid signature / stale / future timestamp → 401
        - the org's own ``organization_id`` is derived from the trusted
          connection row, never from the Zendesk payload.
    """

    connection = await ZendeskOAuthTokenRepository.get_by_integration_id(
        db,
        integration_id,
    )

    if connection is None or connection.organization_id is None:
        log.warning(
            "zendesk_webhook_unknown_integration",
            integration_id=integration_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook endpoint not found.",
        )

    signature = request.headers.get("x-zendesk-webhook-signature")

    timestamp = request.headers.get("x-zendesk-webhook-signature-timestamp")

    invocation_id = request.headers.get("x-zendesk-webhook-invocation-id")

    if not signature or not timestamp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Zendesk webhook signature.",
        )

    if not invocation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Zendesk invocation ID.",
        )

    raw_body = await request.body()

    replay_window = get_settings().zendesk_webhook_replay_window_seconds

    valid = verify_zendesk_signature(
        secret=connection.webhook_secret or "",
        timestamp=timestamp,
        body=raw_body,
        signature=signature,
        replay_window_seconds=replay_window,
    )

    if not valid:
        log.warning(
            "zendesk_webhook_signature_invalid",
            organization_id=connection.organization_id,
            integration_id=integration_id,
            invocation_id=invocation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Zendesk webhook signature.",
        )

    try:
        payload = json.loads(raw_body)

        raw_event_type = str(payload["type"])

        ticket_id = int(payload["detail"]["id"])

        event_type = raw_event_type.removeprefix("zen:event-type:")

    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid Zendesk event payload.",
        )

    return await IntegrationJobService.enqueue_zendesk_event(
        db=db,
        invocation_id=invocation_id,
        event_type=event_type,
        zendesk_ticket_id=ticket_id,
        payload=payload,
        organization_id=connection.organization_id,
    )
