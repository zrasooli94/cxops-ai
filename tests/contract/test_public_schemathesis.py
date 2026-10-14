"""Disposable-only OpenAPI property tests of the nine unauthenticated routes.

Schemathesis talks to the FastAPI ASGI callable, NOT to a network server.
No real widgets are provisioned, bearer tokens are intentionally invalid,
lead capture is disabled, and no provider credentials are available.
This is the deliberately bounded initial spike: unauthenticated malformed
input robustness and documented 2xx/422 response-schema conformance.
"""
from __future__ import annotations

import os

import pytest

from scripts.api_contract_guard import (
    UnsafeContractTarget,
    validate_disposable_contract_env,
)

# Do not even import the application or Schemathesis during the ordinary unit
# suite. An explicit pilot flag without a safe target is an error, never a skip.
if os.environ.get("CXOPS_API_CONTRACT_RUN") != "1":
    pytest.skip("Schemathesis runs in its isolated CI job only", allow_module_level=True)
try:
    validate_disposable_contract_env()
except UnsafeContractTarget as exc:
    pytest.fail(str(exc), pytrace=False)

schemathesis = pytest.importorskip("schemathesis", minversion="4.31.0")

from hypothesis import settings
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app

PUBLIC_OPERATIONS = frozenset({
    ("GET", "/public/chat/config"),
    ("POST", "/public/chat/sessions"),
    ("POST", "/public/chat/messages"),
    ("GET", "/public/chat/sessions/state"),
    ("GET", "/public/chat/sessions/updates"),
    ("POST", "/public/chat/sessions/human"),
    ("POST", "/public/chat/sessions/close"),
    ("GET", "/public/leads/availability"),
    ("POST", "/public/leads"),
})
INVALID_BEARER = "contract-invalid-visitor-token-not-a-secret"
schema = schemathesis.openapi.from_asgi("/openapi.json", app)
public_schema = schema.include(path_regex=r"^/public/(?:chat|leads)(?:/|$)")


def test_public_openapi_inventory_is_explicit_and_reviewable():
    spec = app.openapi()
    operations = {
        (method.upper(), path)
        for path, entry in spec["paths"].items()
        if path.startswith("/public/")
        for method in entry
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    assert operations == PUBLIC_OPERATIONS, (
        "Public surface changed: update the operation allowlist and security review"
    )


@pytest.mark.asyncio
async def test_disposable_database_has_no_provisioned_widget_configuration():
    # A valid key could otherwise reach an AI path; abort the pilot rather
    # than hoping generated input never matches a real tenant's widget.
    async with AsyncSessionLocal() as db:
        count = await db.scalar(text("SELECT COUNT(*) FROM public_chat_configurations"))
    assert count == 0, "API contract DB must be freshly migrated and unseeded"


@public_schema.parametrize()
@settings(max_examples=12, deadline=None, derandomize=True)
def test_anonymous_public_operations_are_robust_to_generated_inputs(case):
    # Schemathesis may deliberately negate the HTTP method (e.g. TRACE)
    # while preserving the original OpenAPI operation. Keep these negative
    # cases: an unregistered method must be rejected, not dispatched.
    assert (case.operation.method.upper(), case.path) in PUBLIC_OPERATIONS
    response = case.call(
        headers={
            "Authorization": f"Bearer {INVALID_BEARER}",
            "X-Embedding-Origin": "https://contract.invalid",
        }
    )
    if case.method.upper() != case.operation.method.upper():
        assert response.status_code == 405, (
            f"Unsupported method {case.method} was not rejected on {case.path}"
        )
    elif response.status_code == 503:
        # An explicitly disabled lead-capture POST returns 503 by contract.
        assert case.path == "/public/leads"
    else:
        assert response.status_code < 500, (
            f"{case.method} {case.path} returned HTTP {response.status_code}"
        )
    assert response.status_code in {200, 202, 400, 401, 403, 404, 405, 409, 413, 422, 429, 503}
    # No stack trace, provider credential or injected bearer in responses.
    body = response.content
    assert b"Traceback (most recent call last)" not in body
    assert INVALID_BEARER.encode() not in body
    assert len(body) <= 262_144
    if response.status_code in {200, 202, 422}:
        # 4xx responses other than FastAPI's documented 422 often have no
        # OpenAPI response entries; tracking those gaps is separate from this
        # no-5xx negative-input pilot.
        # Operation-level validation checks response bodies only. Schemathesis's
        # default case checks also require acceptance of every schema-compliant
        # request; a known URL/domain semantic constraint is not in OpenAPI.
        # Keep that discovery in the review document instead of weakening the
        # application's domain validator.
        case.operation.validate_response(response, case=case)
