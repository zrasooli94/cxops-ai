"""Phase 1U-C: disposable-only, read-only staff API contract and RBAC tests.

All GraphQL/IdP providers, AI, SMTP/Telegram, and production DB are outside
this test. JWTs are locally signed using synthetic in-process material, and
the two staff organizations exist only in a fresh disposable PostgreSQL DB.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select

from scripts.api_contract_guard import (
    UnsafeContractTarget,
    validate_disposable_contract_env,
)

if os.environ.get("CXOPS_API_CONTRACT_RUN") != "1":
    pytest.skip("Only the isolated API contract job runs these checks", allow_module_level=True)
try:
    validate_disposable_contract_env()
except UnsafeContractTarget as exc:
    pytest.fail(str(exc), pytrace=False)
if os.environ.get("AUTH_JWT_SECRET"):
    pytest.fail("Staff contract pilot refuses pre-existing JWT credentials", pytrace=False)

# Only after the disposable-DB and no-credentials guard: establish synthetic
# test authentication. These literal values are not accepted in production.
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ISSUER"] = "staff-contract-disposable"
os.environ["AUTH_JWT_AUDIENCE"] = "staff-contract-disposable"

schemathesis = pytest.importorskip("schemathesis", minversion="4.31.0")

from hypothesis import settings

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.conversation import Conversation
from app.models.organization import LIFECYCLE_ACTIVE, Organization
from app.models.organization_membership import OrganizationMembership, OrganizationRole
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.models.ticket import Ticket

reset_settings_cache()
schema = schemathesis.openapi.from_asgi("/openapi.json", app)
STAFF_OPERATIONS = frozenset({
    ("GET", "/staff/public-chat/summary"),
    ("GET", "/staff/public-chat/handoff"),
    ("GET", "/staff/public-chat/sessions/{session_id}/business-actions"),
})
staff_schema = schema.include(
    path_regex=r"^/staff/public-chat/(?:summary|handoff|sessions/\{session_id\}/business-actions)$"
)


def _token(subject: str) -> str:
    now = int(datetime.now(UTC).timestamp())
    return jwt.encode({
        "sub": subject,
        "iss": os.environ["AUTH_JWT_ISSUER"],
        "aud": os.environ["AUTH_JWT_AUDIENCE"],
        "exp": now + 1800,
        "iat": now,
    }, os.environ["AUTH_JWT_SECRET"], algorithm="HS256")


def _headers(organization_id: int, subject: str = "contract-viewer") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token(subject)}",
        "X-CXOps-Organization-ID": str(organization_id),
    }


async def _seed() -> dict[str, int]:
    async with AsyncSessionLocal() as db:
        nonce = uuid.uuid4().hex[:12]
        own = Organization(name=f"contract-own-{nonce}", lifecycle_status=LIFECYCLE_ACTIVE)
        other = Organization(name=f"contract-other-{nonce}", lifecycle_status=LIFECYCLE_ACTIVE)
        db.add_all([own, other])
        await db.flush()
        db.add_all([
            OrganizationMembership(
                organization_id=own.id, subject="contract-viewer", role=OrganizationRole.VIEWER
            ),
            OrganizationMembership(
                organization_id=own.id, subject="contract-owner", role=OrganizationRole.OWNER
            ),
            OrganizationMembership(
                organization_id=other.id, subject="contract-foreign", role=OrganizationRole.VIEWER
            ),
        ])
        ids = {"own_org": own.id, "other_org": other.id}
        for name, org in (("own", own), ("other", other)):
            cfg = PublicChatConfiguration(
                organization_id=org.id,
                public_widget_key_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                display_name="Synthetic Contract Widget",
                welcome_message="Hello",
                allowed_origins=["https://contract.invalid"],
                enabled=True,
                max_message_length=4000,
                max_messages_per_minute=20,
                session_ttl_hours=24,
            )
            db.add(cfg)
            await db.flush()
            conv = Conversation(
                organization_id=org.id, provider="public_chat",
                channel="public_chat", status="open",
            )
            ticket = Ticket(
                organization_id=org.id, subject="Synthetic contract case",
                description=".", status="open", priority="medium",
                source="public-chat-widget",
            )
            db.add_all([conv, ticket])
            await db.flush()
            session = PublicChatSession(
                organization_id=org.id,
                configuration_id=cfg.id,
                conversation_id=conv.id,
                ticket_id=ticket.id,
                token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                status="human_requested",
                expires_at=datetime.now(UTC) + timedelta(hours=2),
            )
            db.add(session)
            await db.flush()
            ids[f"{name}_session"] = session.id
        await db.commit()
        return ids


async def _unseed(data: dict[str, int]) -> None:
    from app.models.base import Base

    orgs = [data["own_org"], data["other_org"]]
    async with AsyncSessionLocal() as db:
        for table_name in (
            "public_chat_sessions", "public_chat_configurations",
            "conversations", "tickets", "organization_memberships",
        ):
            table = Base.metadata.tables[table_name]
            await db.execute(delete(table).where(table.c.organization_id.in_(orgs)))
        await db.execute(delete(Organization).where(Organization.id.in_(orgs)))
        await db.commit()


@pytest.fixture(scope="module")
def staff_tenants():
    data = asyncio.run(_seed())
    yield data
    asyncio.run(_unseed(data))


def test_staff_openapi_scope_is_exact():
    paths = app.openapi()["paths"]
    actual = {
        (method.upper(), path)
        for path, item in paths.items()
        for method in item
        if method.lower() == "get" and path.startswith("/staff/public-chat/")
    }
    assert STAFF_OPERATIONS == actual


@pytest.mark.asyncio
async def test_viewer_can_read_only_their_tenant(staff_tenants):
    own = staff_tenants["own_org"]
    foreign = staff_tenants["other_org"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        summary = await client.get("/staff/public-chat/summary", headers=_headers(own))
        assert summary.status_code == 200
        assert summary.json()["tenant_id"] == own
        handoff = await client.get("/staff/public-chat/handoff", headers=_headers(own))
        assert handoff.status_code == 200
        visible = [row["session_id"] for row in handoff.json()["sessions"]]
        assert staff_tenants["own_session"] in visible
        assert staff_tenants["other_session"] not in visible
        own_actions = await client.get(
            f"/staff/public-chat/sessions/{staff_tenants['own_session']}/business-actions",
            headers=_headers(own),
        )
        assert own_actions.status_code == 200
        foreign_actions = await client.get(
            f"/staff/public-chat/sessions/{staff_tenants['other_session']}/business-actions",
            headers=_headers(own),
        )
        assert foreign_actions.status_code == 404
        forged_selector = await client.get(
            "/staff/public-chat/handoff",
            headers=_headers(foreign),
        )
        assert forged_selector.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_assign_release_or_resolve(staff_tenants):
    sid = staff_tenants["own_session"]
    own = staff_tenants["own_org"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        for op in ("assign", "release", "resolve"):
            denied = await client.post(
                f"/staff/public-chat/sessions/{sid}/{op}", headers=_headers(own)
            )
            assert denied.status_code == 403
        anonymous = await client.get("/staff/public-chat/handoff")
        assert anonymous.status_code == 401
        foreign_token = await client.get(
            "/staff/public-chat/handoff",
            headers=_headers(own, subject="contract-foreign"),
        )
        assert foreign_token.status_code == 403
    async with AsyncSessionLocal() as db:
        state = await db.scalar(select(PublicChatSession.status).where(PublicChatSession.id == sid))
        assert state == "human_requested"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["0", "-1", "2147483648", "9223372036854775808"])
async def test_invalid_staff_session_ids_never_reach_int32_database_bind(
    staff_tenants, bad_id
):
    """The fuzzer found a 500 at 2147483648: enforce the DB ID bound at HTTP.

    Use an owner, not a denied viewer, to ensure every handler could otherwise
    reach its database query. All four routes must respond with 422.
    """
    own = staff_tenants["own_org"]
    headers = _headers(own, subject="contract-owner")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for op in ("assign", "release", "resolve"):
            response = await client.post(
                f"/staff/public-chat/sessions/{bad_id}/{op}",
                headers=headers,
            )
            assert response.status_code == 422
        actions = await client.get(
            f"/staff/public-chat/sessions/{bad_id}/business-actions",
            headers=headers,
        )
        assert actions.status_code == 422
    async with AsyncSessionLocal() as db:
        assert await db.scalar(
            select(PublicChatSession.status).where(
                PublicChatSession.id == staff_tenants["own_session"]
            )
        ) == "human_requested"


@staff_schema.parametrize()
@settings(max_examples=10, deadline=None, derandomize=True)
def test_authenticated_staff_read_routes_resist_generated_inputs(case, staff_tenants):
    # Only GET operations were selected from OpenAPI; Schemathesis may send
    # negated methods, which must be rejected before entering handlers.
    assert (case.operation.method.upper(), case.operation.path) in STAFF_OPERATIONS
    token = _token("contract-viewer")
    response = case.call(
        headers={
            "Authorization": f"Bearer {token}",
            "X-CXOps-Organization-ID": str(staff_tenants["own_org"]),
        }
    )
    if case.method.upper() != case.operation.method.upper():
        assert response.status_code == 405
    else:
        assert response.status_code < 500, (
            f"{case.operation.method} {case.operation.path} returned HTTP {response.status_code}"
        )
    assert response.status_code in {200, 400, 401, 403, 404, 405, 422}
    assert len(response.content) <= 131_072
    assert b"Traceback (most recent call last)" not in response.content
    assert token.encode() not in response.content
    if response.status_code in (200, 422):
        case.operation.validate_response(response, case=case)
