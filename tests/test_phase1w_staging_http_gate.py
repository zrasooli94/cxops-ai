"""Fail-closed staging HTTP policy, independent of provider/test credentials."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app import main
from staging.http_gate import staging_request_allowed


@pytest.mark.parametrize(
    "path",
    [
        "/health", "/ready", "/version",
        "/me/organizations", "/me/tenant", "/me/authorization",
        "/staff/public-chat/handoff", "/staff/public-chat/summary",
        "/tickets", "/customers",
    ],
)
def test_only_explicit_health_and_authenticated_staff_reads_allowed(path):
    assert staging_request_allowed("GET", path)
    assert staging_request_allowed("HEAD", path)
    assert not staging_request_allowed("POST", path)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/metrics"),
        ("GET", "/public/chat/config"),
        ("POST", "/public/chat/sessions"),
        ("POST", "/webhooks/zendesk/tickets/synthetic"),
        ("POST", "/staff/public-chat/sessions/1/assign"),
        ("POST", "/staff/public-chat/sessions/1/release"),
        ("POST", "/staff/public-chat/sessions/1/resolve"),
        ("GET", "/staff/public-chat/handoff/other"),
        ("GET", "/staff/public-chat/summary/"),
        ("GET", "/staff/public-chat/sessions/1/business-actions"),
        ("POST", "/public/leads"),
        ("GET", "/agent/runs"),
        ("DELETE", "/customers/1"),
        ("OPTIONS", "/me/organizations"),
        ("TRACE", "/health"),
        ("GET", "/health/%2e%2e/metrics"),
    ],
)
def test_every_unreviewed_route_and_method_is_blocked(method, path):
    assert staging_request_allowed(method, path) is False


@pytest.mark.asyncio
async def test_real_staging_asgi_gate_never_calls_provider_or_db_for_blocked_paths(monkeypatch):
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(environment="staging"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
    ) as client:
        for method, path in [
            ("POST", "/staff/public-chat/sessions/42/resolve"),
            ("POST", "/public/chat/sessions"),
            ("POST", "/webhooks/zendesk/tickets/synthetic"),
            ("GET", "/openapi.json"),
            ("GET", "/metrics"),
        ]:
            response = await client.request(method, path, json={"test": "synthetic"})
            assert response.status_code == 503
            assert response.json() == {"detail": "Staging operations are not enabled."}
            assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_production_requests_still_use_existing_pipeline(monkeypatch):
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(environment="production"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/openapi.json")
    assert response.status_code == 200
