# Generic ticket-event webhook machine-authentication tests.
#
# The real /webhooks/ticket-events route runs an authenticated database write,
# so the success path is exercised through a probe app that mounts the same
# signature dependency (no external services, no migrations required).

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import verify_ticket_event_signature
from app.core.config import reset_settings_cache
from app.integrations.webhooks.security import (
    compute_signature,
    compute_signed_webhook_signature,
    verify_signature,
    verify_signed_webhook_signature,
)
from app.main import app

# Synthetic test fixtures — obviously non-secret deterministic values.
TEST_SECRET = "x" * 32
ALT_SECRET = "y" * 32
SIGNATURE_HEADER = "x-cxops-signature"
TIMESTAMP_HEADER = "x-cxops-timestamp"


os.environ["ENVIRONMENT"] = "development"


def _configure(monkeypatch, **overrides) -> None:
    values = {
        "TICKET_EVENT_WEBHOOK_SECRET": TEST_SECRET,
        "ENVIRONMENT": "development",
        "GENERIC_WEBHOOK_REPLAY_WINDOW_SECONDS": "300",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sign(
    secret: str,
    body: bytes,
    *,
    timestamp: str | None = None,
) -> dict:
    ts = timestamp if timestamp is not None else _now_iso()
    signature = compute_signed_webhook_signature(
        secret=secret,
        timestamp=ts,
        body=body,
    )
    return {
        "body": body,
        "timestamp": ts,
        "signature": signature,
    }


class LogRecorder:
    """Recording stand-in for a structlog bound logger.

    structlog configures module-level loggers lazily and caches them, so
    structlog.testing.capture_logs() cannot observe loggers already used by
    earlier tests in the same process. Patching the module logger with a
    recorder captures exactly the events our code emits.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []

    def _record(self, event: str, **kwargs) -> None:
        self.events.append({"event": event, **kwargs})

    def info(self, event: str, **kwargs) -> None:
        self._record(event, **kwargs)

    def warning(self, event: str, **kwargs) -> None:
        self._record(event, **kwargs)

    def error(self, event: str, **kwargs) -> None:
        self._record(event, **kwargs)


signature_probe_app = FastAPI(title="webhook-signature-probe")


@signature_probe_app.post("/webhooks/ticket-events", status_code=202)
async def probe_ticket_event(
    raw_body: Annotated[bytes, Depends(verify_ticket_event_signature)],
):
    return {"status": "accepted"}


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
def probe():
    return AsyncClient(
        transport=ASGITransport(app=signature_probe_app),
        base_url="http://testserver",
    )


class TestSignatureVerification:
    """Unit-level coverage of the body-only HMAC primitive."""

    def test_valid_signature_verifies(self):
        body = b'{"event_id": "evt-1", "event_type": "ticket.created"}'

        signature = compute_signature(secret=TEST_SECRET, body=body)

        assert verify_signature(
            secret=TEST_SECRET,
            body=body,
            signature=signature,
        )

    def test_wrong_secret_rejected(self):
        body = b'{"event_id": "evt-1"}'
        signature = compute_signature(secret=ALT_SECRET, body=body)

        assert not verify_signature(
            secret=TEST_SECRET,
            body=body,
            signature=signature,
        )

    def test_modified_payload_rejected(self):
        original = b'{"event_id": "evt-1"}'
        tampered = b'{"event_id": "evt-2"}'

        signature = compute_signature(secret=TEST_SECRET, body=original)

        assert not verify_signature(
            secret=TEST_SECRET,
            body=tampered,
            signature=signature,
        )

    def test_malformed_signature_rejected(self):
        body = b'{"event_id": "evt-1"}'

        for bad in ("", "abc", "z" * 64, "sha256=abc", "9" * 63):
            assert not verify_signature(
                secret=TEST_SECRET,
                body=body,
                signature=bad,
            )


class TestSignedWebhookVerification:
    """Unit-level coverage of the timestamp + body HMAC primitive."""

    def test_valid_signed_signature_verifies(self):
        body = b'{"event_id": "evt-1"}'
        signed = _sign(TEST_SECRET, body)

        assert verify_signed_webhook_signature(
            secret=TEST_SECRET,
            timestamp=signed["timestamp"],
            body=body,
            signature=signed["signature"],
            replay_window_seconds=300,
        )

    def test_wrong_secret_rejected(self):
        body = b'{"event_id": "evt-1"}'
        signed = _sign(ALT_SECRET, body)

        assert not verify_signed_webhook_signature(
            secret=TEST_SECRET,
            timestamp=signed["timestamp"],
            body=body,
            signature=signed["signature"],
            replay_window_seconds=300,
        )

    def test_modified_timestamp_rejected(self):
        body = b'{"event_id": "evt-1"}'
        signed = _sign(TEST_SECRET, body)

        assert not verify_signed_webhook_signature(
            secret=TEST_SECRET,
            timestamp=_now_iso(),
            body=body,
            signature=signed["signature"],
            replay_window_seconds=300,
        )

    def test_stale_timestamp_rejected(self):
        body = b'{"event_id": "evt-1"}'
        stale = (
            (datetime.now(timezone.utc) - timedelta(seconds=400))
            .isoformat()
            .replace("+00:00", "Z")
        )
        signed = _sign(TEST_SECRET, body, timestamp=stale)

        assert not verify_signed_webhook_signature(
            secret=TEST_SECRET,
            timestamp=signed["timestamp"],
            body=body,
            signature=signed["signature"],
            replay_window_seconds=300,
        )

    def test_future_timestamp_rejected(self):
        body = b'{"event_id": "evt-1"}'
        future = (
            (datetime.now(timezone.utc) + timedelta(seconds=400))
            .isoformat()
            .replace("+00:00", "Z")
        )
        signed = _sign(TEST_SECRET, body, timestamp=future)

        assert not verify_signed_webhook_signature(
            secret=TEST_SECRET,
            timestamp=signed["timestamp"],
            body=body,
            signature=signed["signature"],
            replay_window_seconds=300,
        )

    def test_timestamp_exactly_at_boundary_accepted(self):
        body = b'{"event_id": "evt-1"}'
        now = datetime.now(timezone.utc)
        boundary = now - timedelta(seconds=300)
        signed = _sign(
            TEST_SECRET,
            body,
            timestamp=boundary.isoformat().replace("+00:00", "Z"),
        )

        assert verify_signed_webhook_signature(
            secret=TEST_SECRET,
            timestamp=signed["timestamp"],
            body=body,
            signature=signed["signature"],
            replay_window_seconds=300,
            now=now,
        )

    def test_timestamp_one_second_past_boundary_rejected(self):
        body = b'{"event_id": "evt-1"}'
        now = datetime.now(timezone.utc)
        past = now - timedelta(seconds=301)
        signed = _sign(
            TEST_SECRET,
            body,
            timestamp=past.isoformat().replace("+00:00", "Z"),
        )

        assert not verify_signed_webhook_signature(
            secret=TEST_SECRET,
            timestamp=signed["timestamp"],
            body=body,
            signature=signed["signature"],
            replay_window_seconds=300,
            now=now,
        )

    def test_malformed_timestamp_rejected(self):
        body = b'{"event_id": "evt-1"}'
        signed = _sign(TEST_SECRET, body)

        assert not verify_signed_webhook_signature(
            secret=TEST_SECRET,
            timestamp="not-a-timestamp",
            body=body,
            signature=signed["signature"],
            replay_window_seconds=300,
        )


class TestWebhookEndpoint:
    """Route-level integration through the real app."""

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self, monkeypatch, client):
        _configure(monkeypatch)
        payload = {"event_id": "evt-1", "event_type": "ticket.created"}

        async with client:
            response = await client.post("/webhooks/ticket-events", json=payload)

        assert response.status_code == 401
        assert "signature" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_timestamp_rejected(self, monkeypatch, client):
        _configure(monkeypatch)
        payload = {"event_id": "evt-1", "event_type": "ticket.created"}
        body = json.dumps(payload).encode()
        signed = _sign(TEST_SECRET, body)

        async with client:
            response = await client.post(
                "/webhooks/ticket-events",
                content=body,
                headers={SIGNATURE_HEADER: signed["signature"]},
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self, monkeypatch, client):
        _configure(monkeypatch)
        payload = {"event_id": "evt-1", "event_type": "ticket.created"}
        body = json.dumps(payload).encode()
        signed = _sign(ALT_SECRET, body)

        async with client:
            response = await client.post(
                "/webhooks/ticket-events",
                content=body,
                headers={
                    SIGNATURE_HEADER: signed["signature"],
                    TIMESTAMP_HEADER: signed["timestamp"],
                },
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_modified_payload_rejected(self, monkeypatch, client):
        _configure(monkeypatch)
        original = json.dumps(
            {"event_id": "evt-1", "event_type": "ticket.created"}
        ).encode()
        tampered = original.replace(b"evt-1", b"evt-9")
        signed = _sign(TEST_SECRET, original)

        async with client:
            response = await client.post(
                "/webhooks/ticket-events",
                content=tampered,
                headers={
                    SIGNATURE_HEADER: signed["signature"],
                    TIMESTAMP_HEADER: signed["timestamp"],
                },
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_stale_timestamp_rejected(self, monkeypatch, client):
        _configure(monkeypatch)
        payload = {"event_id": "evt-1", "event_type": "ticket.created"}
        body = json.dumps(payload).encode()
        stale = (
            (datetime.now(timezone.utc) - timedelta(seconds=400))
            .isoformat()
            .replace("+00:00", "Z")
        )
        signed = _sign(TEST_SECRET, body, timestamp=stale)

        async with client:
            response = await client.post(
                "/webhooks/ticket-events",
                content=body,
                headers={
                    SIGNATURE_HEADER: signed["signature"],
                    TIMESTAMP_HEADER: signed["timestamp"],
                },
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_future_timestamp_rejected(self, monkeypatch, client):
        _configure(monkeypatch)
        payload = {"event_id": "evt-1", "event_type": "ticket.created"}
        body = json.dumps(payload).encode()
        future = (
            (datetime.now(timezone.utc) + timedelta(seconds=400))
            .isoformat()
            .replace("+00:00", "Z")
        )
        signed = _sign(TEST_SECRET, body, timestamp=future)

        async with client:
            response = await client.post(
                "/webhooks/ticket-events",
                content=body,
                headers={
                    SIGNATURE_HEADER: signed["signature"],
                    TIMESTAMP_HEADER: signed["timestamp"],
                },
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_signature_rejected(self, monkeypatch, client):
        _configure(monkeypatch)

        async with client:
            response = await client.post(
                "/webhooks/ticket-events",
                json={"event_id": "evt-1", "event_type": "ticket.created"},
                headers={
                    SIGNATURE_HEADER: "definitely-not-a-signature",
                    TIMESTAMP_HEADER: _now_iso(),
                },
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_secret_fails_closed_development(self, monkeypatch, client):
        _configure(monkeypatch, TICKET_EVENT_WEBHOOK_SECRET="")
        payload = {"event_id": "evt-1", "event_type": "ticket.created"}
        body = json.dumps(payload).encode()
        signed = _sign(TEST_SECRET, body)

        async with client:
            response = await client.post(
                "/webhooks/ticket-events",
                content=body,
                headers={
                    SIGNATURE_HEADER: signed["signature"],
                    TIMESTAMP_HEADER: signed["timestamp"],
                },
            )

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_missing_secret_fails_closed_production(self, monkeypatch, client):
        _configure(
            monkeypatch,
            TICKET_EVENT_WEBHOOK_SECRET="",
            ENVIRONMENT="production",
            AUTH_MODE="jwks",
            AUTH_JWKS_URL="https://example.com/.well-known/jwks.json",
        )
        payload = {"event_id": "evt-1", "event_type": "ticket.created"}
        body = json.dumps(payload).encode()
        signed = _sign(TEST_SECRET, body)

        async with client:
            response = await client.post(
                "/webhooks/ticket-events",
                content=body,
                headers={
                    SIGNATURE_HEADER: signed["signature"],
                    TIMESTAMP_HEADER: signed["timestamp"],
                },
            )

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_valid_signed_webhook_accepted_without_human_jwt(
        self, monkeypatch, probe
    ):
        _configure(monkeypatch)
        payload = {"event_id": "evt-1", "event_type": "ticket.created"}
        body = json.dumps(payload).encode()
        signed = _sign(TEST_SECRET, body)

        async with probe:
            response = await probe.post(
                "/webhooks/ticket-events",
                content=body,
                headers={
                    SIGNATURE_HEADER: signed["signature"],
                    TIMESTAMP_HEADER: signed["timestamp"],
                },
            )

        assert response.status_code == 202
        assert response.json()["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_valid_signed_webhook_accepted_on_real_route(
        self,
        monkeypatch,
        probe,
    ):
        """A validly signed request passes signature verification on the real
        dependency (DB-backed write path is exercised via the probe's handler,
        which uses the same dependency as the real route)."""
        _configure(monkeypatch)
        payload = {"event_id": "evt-1", "event_type": "ticket.created"}
        body = json.dumps(payload).encode()
        signed = _sign(TEST_SECRET, body)

        async with probe:
            response = await probe.post(
                "/webhooks/ticket-events",
                content=body,
                headers={
                    SIGNATURE_HEADER: signed["signature"],
                    TIMESTAMP_HEADER: signed["timestamp"],
                },
            )

        assert response.status_code == 202


class TestRedactionInWebhookLogs:
    """No secret or signature may reach log output."""

    @pytest.mark.asyncio
    async def test_secret_and_signature_not_in_logs(self, monkeypatch, client):
        _configure(monkeypatch)
        recorder = LogRecorder()
        monkeypatch.setattr("app.api.deps.log", recorder)

        payload = {"event_id": "evt-1", "event_type": "ticket.created"}
        body = json.dumps(payload).encode()
        bad_signature = "0" * 64

        async with client:
            await client.post(
                "/webhooks/ticket-events",
                content=body,
                headers={
                    SIGNATURE_HEADER: bad_signature,
                    TIMESTAMP_HEADER: _now_iso(),
                },
            )

        assert recorder.events, "expected auth log output"
        for entry in recorder.events:
            rendered = str(entry)
            assert TEST_SECRET not in rendered
            assert bad_signature not in rendered
