"""Phase 1U-H: synthetic replay regression reproducer (no real Zendesk or DB).

This test intentionally documents vulnerable master behavior. It should be
updated to expect one durable job when replay prevention is implemented.
"""
import base64
import hashlib
import hmac
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.repositories.zendesk_oauth_token_repository import ZendeskOAuthTokenRepository
from app.services.integration_job_service import IntegrationJobService


@pytest.mark.asyncio
async def test_relabelled_unsigned_invocation_enqueues_twice_on_current_contract(monkeypatch):
    secret = "synthetic-replay-secret-not-for-production"
    queued = []

    async def fake_connection(_db, integration_id):
        assert integration_id == "synthetic-connection"
        return SimpleNamespace(organization_id=42, webhook_secret=secret)

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {
            "event_id": kwargs["invocation_id"],
            "job_id": len(queued),
            "duplicate": False,
            "status": "queued",
        }

    monkeypatch.setattr(
        ZendeskOAuthTokenRepository, "get_by_integration_id",
        staticmethod(fake_connection),
    )
    monkeypatch.setattr(
        IntegrationJobService, "enqueue_zendesk_event",
        staticmethod(fake_enqueue),
    )

    body = b'{"type":"zen:event-type:ticket.created","detail":{"id":123}}'
    timestamp = datetime.now(UTC).isoformat()
    signature = base64.b64encode(
        hmac.new(
            secret.encode(), timestamp.encode() + body, hashlib.sha256
        ).digest()
    ).decode()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        responses = []
        for invocation_id in ("captured-delivery", "relabelled-delivery"):
            response = await client.post(
                "/webhooks/zendesk/tickets/synthetic-connection",
                content=body,
                headers={
                    "X-Zendesk-Webhook-Signature": signature,
                    "X-Zendesk-Webhook-Signature-Timestamp": timestamp,
                    "X-Zendesk-Webhook-Invocation-Id": invocation_id,
                },
            )
            responses.append(response)

    assert [response.status_code for response in responses] == [202, 202]
    assert [item["invocation_id"] for item in queued] == [
        "captured-delivery", "relabelled-delivery"
    ]
    assert all(item["organization_id"] == 42 for item in queued)
    # Not a database-concurrency or writeback-impact test: enqueue is mocked.
