import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.request_context import (
    RequestCorrelationMiddleware,
    get_request_id,
    sanitize_request_id,
)
from app.main import app


class DummyApp:
    def __init__(self):
        self.called = False
        self.received_request_id = None

    async def __call__(self, scope, receive, send):
        self.called = True
        self.received_request_id = get_request_id()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"ok",
            }
        )


@pytest.mark.asyncio
async def test_middleware_generates_request_id_when_absent():
    dummy = DummyApp()
    middleware = RequestCorrelationMiddleware(dummy)

    scope = {"type": "http", "headers": []}
    receive = AsyncMock()
    send = AsyncMock()

    await middleware(scope, receive, send)

    assert dummy.called
    assert dummy.received_request_id is not None
    assert len(dummy.received_request_id) == 32
    assert uuid.UUID(dummy.received_request_id).version == 4


@pytest.mark.asyncio
async def test_middleware_accepts_valid_incoming_request_id():
    dummy = DummyApp()
    middleware = RequestCorrelationMiddleware(dummy)

    valid_id = "abc123.valid-id_456"
    scope = {
        "type": "http",
        "headers": [(b"x-request-id", valid_id.encode("latin-1"))],
    }
    receive = AsyncMock()
    send = AsyncMock()

    await middleware(scope, receive, send)

    assert dummy.called
    assert dummy.received_request_id == valid_id


@pytest.mark.asyncio
async def test_middleware_rejects_invalid_request_id():
    dummy = DummyApp()
    middleware = RequestCorrelationMiddleware(dummy)

    scope = {
        "type": "http",
        "headers": [(b"x-request-id", b"invalid id with spaces")],
    }
    receive = AsyncMock()
    send = AsyncMock()

    await middleware(scope, receive, send)

    assert dummy.called
    assert dummy.received_request_id is not None
    assert dummy.received_request_id != "invalid id with spaces"
    assert len(dummy.received_request_id) == 32


@pytest.mark.asyncio
async def test_middleware_rejects_too_long_request_id():
    dummy = DummyApp()
    middleware = RequestCorrelationMiddleware(dummy)

    too_long = "a" * 200
    scope = {
        "type": "http",
        "headers": [(b"x-request-id", too_long.encode("latin-1"))],
    }
    receive = AsyncMock()
    send = AsyncMock()

    await middleware(scope, receive, send)

    assert dummy.called
    assert dummy.received_request_id is not None
    assert dummy.received_request_id != too_long


@pytest.mark.asyncio
async def test_middleware_returns_request_id_in_response_header():
    dummy = DummyApp()
    middleware = RequestCorrelationMiddleware(dummy)

    captured_headers = []

    async def capture_send(message):
        if message["type"] == "http.response.start":
            captured_headers.extend(message.get("headers", []))

    scope = {"type": "http", "headers": []}
    receive = AsyncMock()
    send = AsyncMock(side_effect=capture_send)

    await middleware(scope, receive, send)

    header_names = [name.decode("latin-1") for name, _ in captured_headers]
    assert "x-request-id" in header_names


@pytest.mark.asyncio
async def test_middleware_does_not_add_duplicate_header():
    dummy = DummyApp()
    middleware = RequestCorrelationMiddleware(dummy)

    captured_headers = []

    async def capture_send(message):
        if message["type"] == "http.response.start":
            captured_headers.extend(message.get("headers", []))

    scope = {
        "type": "http",
        "headers": [(b"x-request-id", b"pre-existing-id")],
    }
    receive = AsyncMock()
    send = AsyncMock(side_effect=capture_send)

    await middleware(scope, receive, send)

    xrid_count = sum(1 for name, _ in captured_headers if name == b"x-request-id")
    assert xrid_count == 1


@pytest.mark.asyncio
async def test_middleware_clears_contextvars_on_exception():
    from app.core import request_context as rc

    original = rc.request_id_var

    async def failing_app(scope, receive, send):
        rc.request_id_var.set("test-id")
        raise RuntimeError("boom")

    middleware = RequestCorrelationMiddleware(failing_app)

    scope = {"type": "http", "headers": []}
    receive = AsyncMock()
    send = AsyncMock()

    with pytest.raises(RuntimeError, match="boom"):
        await middleware(scope, receive, send)

    assert original.get() is None


def test_sanitize_request_id_accepts_valid_formats():
    assert sanitize_request_id("abc123") == "abc123"
    assert sanitize_request_id("abc-123_def.456") == "abc-123_def.456"
    assert sanitize_request_id("A" * 128) == "A" * 128


def test_sanitize_request_id_rejects_invalid():
    assert sanitize_request_id("") is None
    assert sanitize_request_id(None) is None
    assert sanitize_request_id("invalid id") is None
    assert sanitize_request_id("a" * 129) is None
    assert sanitize_request_id("!@#$%") is None


@pytest.mark.asyncio
async def test_fastapi_app_includes_correlation_middleware():
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) == 32


@pytest.mark.asyncio
async def test_fastapi_app_echoes_valid_request_id():
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            "/health",
            headers={"x-request-id": "test-correlation-123"},
        )
        assert resp.status_code == 200
        assert resp.headers["x-request-id"] == "test-correlation-123"


@pytest.mark.asyncio
async def test_fastapi_app_generates_on_invalid_request_id():
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            "/health",
            headers={"x-request-id": "bad@id!"},
        )
        assert resp.status_code == 200
        assert resp.headers["x-request-id"] != "bad@id!"
        assert len(resp.headers["x-request-id"]) == 32
