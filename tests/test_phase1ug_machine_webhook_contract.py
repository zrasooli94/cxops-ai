"""Phase 1U-G: documentation-only machine authentication & replay contracts.

HTTP probes use in-process ASGI and synthetic, non-secret signing material.
Zendesk token lookup and job enqueue are faked: nothing is sent, persisted, or
fetched from a real external provider. The signed content stays synthetic.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import ASGITransport, AsyncClient

from app.api.deps import (
    get_current_principal_dep,
    verify_ticket_event_signature,
)
from app.api.routes.public_chat import require_session_token
from app.api.routes.zendesk_webhooks import receive_zendesk_ticket_webhook
from app.core.config import reset_settings_cache
from app.core.openapi_security import (
    CHAT_BEARER_NAME,
    GENERIC_HMAC_NAME,
    MACHINE_HEADERS,
    MACHINE_METADATA,
    MACHINE_RESPONSES,
    MACHINE_SECURITY_SCHEMES,
    STAFF_BEARER_NAME,
    ZENDESK_HMAC_NAME,
    _has_dependency,
)
from app.integrations.webhooks.security import (
    compute_signed_webhook_signature,
    verify_signed_webhook_signature,
)
from app.integrations.zendesk.security import verify_zendesk_signature
from app.main import app
from app.repositories.zendesk_oauth_token_repository import ZendeskOAuthTokenRepository
from app.services.integration_job_service import IntegrationJobService

GENERIC_SECRET = "x" * 32
ZENDESK_SECRET = "y" * 32
NOW = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
WEBHOOK_PATHS = (
    ("/webhooks/ticket-events", GENERIC_HMAC_NAME),
    ("/webhooks/zendesk/tickets/{integration_id}", ZENDESK_HMAC_NAME),
)


def _generic_sign(body: bytes, timestamp: str, *, secret: str = GENERIC_SECRET) -> str:
    return compute_signed_webhook_signature(
        secret=secret, timestamp=timestamp, body=body
    )


def _zendesk_sign(body: bytes, timestamp: str, *, secret: str = ZENDESK_SECRET) -> str:
    return base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("utf-8") + body,
            hashlib.sha256,
        ).digest()
    ).decode("ascii")


def test_two_webhook_schemes_are_distinct_from_staff_and_visitor_bearers():
    spec = app.openapi()
    assert "security" not in spec
    schemes = spec["components"]["securitySchemes"]
    for path, name in WEBHOOK_PATHS:
        operation = spec["paths"][path]["post"]
        assert operation["security"] == [{name: []}]
        definition = schemes[name]
        assert definition == MACHINE_SECURITY_SCHEMES[name]
        assert definition["type"] == "apiKey"
        assert definition["in"] == "header"
        assert definition["name"] != "Authorization"
        assert {STAFF_BEARER_NAME: []} not in operation["security"]
        assert {CHAT_BEARER_NAME: []} not in operation["security"]
        # Mentioning that staff JWTs do not grant webhook access is correct
        # documentation. Security classification itself must stay apiKey.
        assert definition["type"] != "http"

    assert schemes[GENERIC_HMAC_NAME]["name"] == "X-CXOps-Signature"
    assert schemes[ZENDESK_HMAC_NAME]["name"] == "X-Zendesk-Webhook-Signature"
    assert "hexadecimal" in schemes[GENERIC_HMAC_NAME]["description"]
    assert "Base64" in schemes[ZENDESK_HMAC_NAME]["description"]
    assert "NOT included" in schemes[ZENDESK_HMAC_NAME]["description"]


@pytest.mark.parametrize(("path", "scheme"), WEBHOOK_PATHS)
def test_required_headers_replay_window_and_failure_codes_are_explicit(path, scheme):
    operation = app.openapi()["paths"][path]["post"]
    params = operation.get("parameters", [])
    for name, description in MACHINE_HEADERS[scheme]:
        matching = [
            p for p in params
            if p.get("in") == "header" and p.get("name") == name
        ]
        assert len(matching) == 1, (path, name)
        parameter = matching[0]
        assert parameter["required"] is True
        assert parameter["description"] == description
        assert parameter["schema"]["minLength"] == 1

    for key, value in MACHINE_METADATA[scheme].items():
        assert operation[key] == value
    for status in MACHINE_RESPONSES[scheme]:
        assert status in operation["responses"]

    # No leaked signing key values, no staff or visitor credential path.
    serialized = json.dumps(operation)
    assert GENERIC_SECRET not in serialized
    assert ZENDESK_SECRET not in serialized
    assert "Authorization: Bearer " not in serialized


def test_actual_router_dependency_graph_matches_every_documented_machine_scheme():
    expected = set(WEBHOOK_PATHS)
    seen: set[tuple[str, str]] = set()
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.include_in_schema:
            continue
        generic = _has_dependency(route.dependant, verify_ticket_event_signature)
        zendesk = route.dependant.call is receive_zendesk_ticket_webhook
        staff = _has_dependency(route.dependant, get_current_principal_dep)
        visitor = _has_dependency(route.dependant, require_session_token)
        assert sum((generic, zendesk, staff, visitor)) <= 1
        if generic or zendesk:
            name = GENERIC_HMAC_NAME if generic else ZENDESK_HMAC_NAME
            assert "POST" in route.methods
            seen.add((route.path_format, name))
            assert app.openapi()["paths"][route.path_format]["post"]["security"] == [
                {name: []}
            ]
    assert seen == expected


def test_public_oauth_callback_and_signed_webhooks_are_not_bearer_protected():
    spec = app.openapi()
    for path, method in (
        ("/auth/zendesk/callback", "get"),
        ("/public/leads", "post"),
        ("/public/chat/sessions", "post"),
    ):
        assert not spec["paths"][path][method].get("security")
    assert app.openapi() is app.openapi(), "schema cache must remain idempotent"


@pytest.mark.parametrize("delta_seconds", [-300, -1, 0, 1, 300])
def test_generic_hmac_window_accepts_only_in_bound_signatures(delta_seconds):
    body = b'{"event_id":"synthetic-1","event_type":"ticket.created"}'
    timestamp = (NOW + timedelta(seconds=delta_seconds)).isoformat()
    signature = _generic_sign(body, timestamp)
    assert verify_signed_webhook_signature(
        secret=GENERIC_SECRET, timestamp=timestamp, body=body,
        signature=signature, replay_window_seconds=300, now=NOW,
    )
    # The actual verifier accepts upper-case hex, too. Do not document an
    # inaccurate lower-case-only input requirement.
    assert verify_signed_webhook_signature(
        secret=GENERIC_SECRET, timestamp=timestamp, body=body,
        signature=signature.upper(), replay_window_seconds=300, now=NOW,
    )


@pytest.mark.parametrize("delta_seconds", [-301, 301])
def test_generic_hmac_replay_outside_window_is_denied(delta_seconds):
    body = b'{"event_id":"synthetic-1"}'
    timestamp = (NOW + timedelta(seconds=delta_seconds)).isoformat()
    assert not verify_signed_webhook_signature(
        secret=GENERIC_SECRET,
        timestamp=timestamp,
        body=body,
        signature=_generic_sign(body, timestamp),
        replay_window_seconds=300,
        now=NOW,
    )


def test_generic_hmac_signs_exact_timestamp_and_body_and_needs_secret():
    body = b'{"event_id":"synthetic-1"}'
    timestamp = NOW.isoformat().replace("+00:00", "Z")
    signature = _generic_sign(body, timestamp)
    common = {
        "secret": GENERIC_SECRET,
        "timestamp": timestamp,
        "body": body,
        "signature": signature,
        "replay_window_seconds": 300,
        "now": NOW,
    }
    assert verify_signed_webhook_signature(**common)
    assert not verify_signed_webhook_signature(**{**common, "body": body + b" "})
    assert not verify_signed_webhook_signature(
        **{**common, "timestamp": timestamp.replace("Z", "+00:00")}
    )
    assert not verify_signed_webhook_signature(**{**common, "secret": ""})
    assert not verify_signed_webhook_signature(**{**common, "signature": "oops"})


@pytest.mark.parametrize("delta_seconds", [-300, 0, 300])
def test_zendesk_base64_hmac_within_window_is_valid(delta_seconds):
    body = b'{"type":"zen:event-type:ticket.created","detail":{"id":99}}'
    timestamp = (NOW + timedelta(seconds=delta_seconds)).isoformat()
    assert verify_zendesk_signature(
        secret=ZENDESK_SECRET, timestamp=timestamp, body=body,
        signature=_zendesk_sign(body, timestamp),
        replay_window_seconds=300, now=NOW,
    )


@pytest.mark.parametrize("delta_seconds", [-301, 301])
def test_zendesk_base64_hmac_expires_outside_replay_window(delta_seconds):
    body = b'{"type":"zen:event-type:ticket.created","detail":{"id":99}}'
    timestamp = (NOW + timedelta(seconds=delta_seconds)).isoformat()
    assert not verify_zendesk_signature(
        secret=ZENDESK_SECRET, timestamp=timestamp, body=body,
        signature=_zendesk_sign(body, timestamp),
        replay_window_seconds=300, now=NOW,
    )


def test_zendesk_signature_cannot_be_replaced_by_generic_hex_or_staff_jwt():
    body = b'{"type":"zen:event-type:ticket.created","detail":{"id":99}}'
    timestamp = NOW.isoformat()
    signature = _zendesk_sign(body, timestamp)
    args = {
        "secret": ZENDESK_SECRET,
        "timestamp": timestamp,
        "body": body,
        "signature": signature,
        "replay_window_seconds": 300,
        "now": NOW,
    }
    assert verify_zendesk_signature(**args)
    assert not verify_zendesk_signature(**{**args, "signature": _generic_sign(body, timestamp)})
    assert not verify_zendesk_signature(**{**args, "secret": ""})
    assert not verify_zendesk_signature(**{**args, "body": body + b"\n"})
    assert not verify_zendesk_signature(**{**args, "timestamp": timestamp + "Z"})
    assert not verify_zendesk_signature(**{**args, "signature": "staff-jwt-token"})


def test_zendesk_invocation_header_is_idempotency_not_signed_input():
    """A signed request can be replayed during its time window.

    Re-labeling the *unsigned* invocation header does not alter HMAC
    verification. This known limitation needs an independent remediation
    milestone; do not misrepresent the original contract as replay-proof.
    """
    body = b'{"type":"zen:event-type:ticket.created","detail":{"id":99}}'
    timestamp = NOW.isoformat()
    signature = _zendesk_sign(body, timestamp)
    # Both replay attempts carry the SAME signed bytes but could carry
    # different, unsigned invocation IDs. The verifier has no nonce argument.
    for invocation in ("delivery-A", "delivery-B"):
        assert invocation
        assert verify_zendesk_signature(
            secret=ZENDESK_SECRET, timestamp=timestamp, body=body,
            signature=signature, replay_window_seconds=300, now=NOW,
        )


@pytest.fixture
def generic_webhook_config(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("TICKET_EVENT_WEBHOOK_SECRET", GENERIC_SECRET)
    monkeypatch.setenv("GENERIC_WEBHOOK_REPLAY_WINDOW_SECONDS", "300")
    reset_settings_cache()
    yield
    reset_settings_cache()


probe_app = FastAPI(title="machine-hmac-security-probe")


@probe_app.post("/probe", status_code=202)
async def _hmac_probe(
    _raw: Annotated[bytes, Depends(verify_ticket_event_signature)],
):
    return {"accepted": True}


@pytest.mark.asyncio
async def test_generic_route_authentication_never_accepts_staff_bearer_alone(
    generic_webhook_config,
):
    body = b'{"event_id":"synthetic-1"}'
    timestamp = datetime.now(UTC).isoformat()
    sig = _generic_sign(body, timestamp)
    async with AsyncClient(
        transport=ASGITransport(app=probe_app), base_url="http://testserver"
    ) as client:
        missing = await client.post(
            "/probe", content=body,
            headers={"Authorization": "Bearer pretend-staff-jwt"},
        )
        assert missing.status_code == 401
        good = await client.post(
            "/probe", content=body,
            headers={
                "X-CXOps-Timestamp": timestamp,
                "X-CXOps-Signature": sig,
            },
        )
        assert good.status_code == 202
        tampered = await client.post(
            "/probe", content=body + b" ",
            headers={
                "X-CXOps-Timestamp": timestamp,
                "X-CXOps-Signature": sig,
            },
        )
        assert tampered.status_code == 401


@pytest.mark.asyncio
async def test_zendesk_real_route_rejects_invalid_signature_before_any_enqueue(monkeypatch):
    """Mock the lookup and enqueue at their DB boundaries, not the verifier."""
    secret = ZENDESK_SECRET
    incoming_id = "synthetic-connection"
    calls: list[dict] = []

    async def fake_connection_lookup(_db, integration_id):
        if integration_id != incoming_id:
            return None
        return SimpleNamespace(organization_id=42, webhook_secret=secret)

    async def fake_enqueue(**kwargs):
        calls.append(kwargs)
        return {
            "event_id": kwargs["invocation_id"],
            "job_id": 777,
            "duplicate": False,
            "status": "queued",
        }

    monkeypatch.setattr(
        ZendeskOAuthTokenRepository, "get_by_integration_id",
        staticmethod(fake_connection_lookup),
    )
    monkeypatch.setattr(
        IntegrationJobService, "enqueue_zendesk_event",
        staticmethod(fake_enqueue),
    )

    body = b'{"type":"zen:event-type:ticket.created","detail":{"id":42}}'
    timestamp = datetime.now(UTC).isoformat()
    signature = _zendesk_sign(body, timestamp)
    headers = {
        "X-Zendesk-Webhook-Signature-Timestamp": timestamp,
        "X-Zendesk-Webhook-Signature": signature,
        "X-Zendesk-Webhook-Invocation-Id": "synthetic-invocation-1",
        "Authorization": "Bearer pretend-staff-jwt",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        unknown = await client.post(
            "/webhooks/zendesk/tickets/unknown", content=body, headers=headers
        )
        assert unknown.status_code == 404
        invalid = await client.post(
            f"/webhooks/zendesk/tickets/{incoming_id}",
            content=body + b" ",
            headers=headers,
        )
        assert invalid.status_code == 401
        stale_timestamp = (datetime.now(UTC) - timedelta(seconds=420)).isoformat()
        stale = await client.post(
            f"/webhooks/zendesk/tickets/{incoming_id}",
            content=body,
            headers={
                **headers,
                "X-Zendesk-Webhook-Signature-Timestamp": stale_timestamp,
                "X-Zendesk-Webhook-Signature": _zendesk_sign(body, stale_timestamp),
            },
        )
        assert stale.status_code == 401
        no_invocation = await client.post(
            f"/webhooks/zendesk/tickets/{incoming_id}",
            content=body,
            headers={
                key: value for key, value in headers.items()
                if key != "X-Zendesk-Webhook-Invocation-Id"
            },
        )
        assert no_invocation.status_code == 400
        assert calls == []
        accepted = await client.post(
            f"/webhooks/zendesk/tickets/{incoming_id}",
            content=body,
            headers=headers,
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["event_id"] == "synthetic-invocation-1"
    assert len(calls) == 1
    assert calls[0]["organization_id"] == 42
    assert calls[0]["zendesk_ticket_id"] == 42
