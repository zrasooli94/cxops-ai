"""Phase 1P.2 — business tools, staff handoff, and DB-rate limiting.

This suite proves the security-critical properties of the second-phase
internals over the real HTTP routes and the real service/DB seams:

- business tool availability derives from tenant enablement rows and fails
  closed (a tool whose provider is not enabled is never executed, even when a
  persisted plan names it)
- durable BusinessAction execution is idempotent by run + tool and never
  trusts tenant/org/conversation ids in tool arguments; it mirrors
  customer-visible results into the local conversation as exactly one
  outbound message
- staff assignment/release is capability-gated, tenant-scoped, and
  transition-safe (human_requested <-> human_assigned); its listings never
  leak a foreign tenant's rows
- the DB rate limiter charges only accepted atomic increments: a bucket at
  its limit rejects without incrementing past the cap

The agent decision LLM is always a capturing deterministic fake and knowledge
search is short-circuited (never OpenAI, never a real Zendesk connection).
"""

import asyncio
import os
import secrets
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-phase1p2-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-phase1p2-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.agent_run import AgentRun
from app.models.base import Base
from app.models.conversation_message import ConversationMessage
from app.models.organization import Organization
from app.models.organization_membership import (
    OrganizationMembership,
    OrganizationRole,
)
from app.models.public_chat import (
    PublicChatConfiguration,
    PublicChatSession,
)
from app.models.ticket import Ticket
from app.repositories.business_action_repository import (
    BusinessActionRepository,
)
from app.services.agent_execution_service import (
    FORBIDDEN_TOOL_ARGUMENT_IDENTITY_FIELDS,
    AgentExecutionError,
    AgentExecutionService,
)
from app.services.business_action_service import (
    BusinessActionPlanError,
    business_action_service,
)
from app.services.business_integration_service import (
    BusinessIntegrationService,
)
from app.services.conversation_ingestion_service import LOCAL_PROVIDER
from app.services.public_chat_rate_limiter import (
    PublicChatRateLimitBucket,
    public_chat_rate_limiter_db,
)
from app.services.public_chat_service import (
    hash_digest,
    public_chat_service,
)
from app.services.public_chat_staff_service import (
    PublicChatStaffAssignmentError,
    PublicChatStaffError,
    PublicChatStaffService,
    PublicChatStaffSessionNotFoundError,
)
from app.services.tool_authorization_service import (
    ToolAuthorizationService,
)

ALLOWED_ORIGIN = "https://widget.example.test"
X_ORIGIN = "X-Embedding-Origin"
TENANT_HEADER = "x-cxops-organization-id"

P1P2_ORG_PREFIX = "phase1p2-test-"
PROVIDER_A1 = "a1_cash_for_cars"

_PURGE_TABLES = [
    "public_chat_sessions",
    "public_chat_configurations",
    "business_actions",
    "business_integration_configurations",
    "ticket_events",
    "agent_action_events",
    "integration_jobs",
    "agent_runs",
    "conversation_messages",
    "conversations",
    "tickets",
    "zendesk_oauth_tokens",
    "zendesk_oauth_states",
    "organization_memberships",
    "organizations",
]

# ----------------------------------------------------------------------
# Fixtures and namespace isolation
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _configure_auth(monkeypatch):
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "z" * 32,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": "test-phase1p2-issuer",
        "AUTH_JWT_AUDIENCE": "test-phase1p2-audience",
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture(scope="module", autouse=True)
def _p1p2_namespace_isolated():
    asyncio.run(_purge_p1p2_namespace())
    yield
    asyncio.run(_assert_p1p2_namespace_clean())


async def _purge_p1p2_namespace() -> list[int]:
    async with AsyncSessionLocal() as session:
        org_ids = [
            org_id
            for (org_id,) in await session.execute(
                select(Organization.id).where(
                    Organization.name.like(f"{P1P2_ORG_PREFIX}%")
                )
            )
        ]
        if org_ids:
            for table_name in _PURGE_TABLES:
                table = Base.metadata.tables.get(table_name)
                if table is not None and "organization_id" in table.columns:
                    await session.execute(
                        delete(table).where(table.c.organization_id.in_(org_ids))
                    )
            await session.execute(
                delete(Organization).where(Organization.id.in_(org_ids))
            )
        await session.execute(
            delete(PublicChatRateLimitBucket).where(
                PublicChatRateLimitBucket.key_cache.like("test:%")
            )
        )
        await session.commit()
        return org_ids


async def _assert_p1p2_namespace_clean() -> None:
    async with AsyncSessionLocal() as session:
        leftover = await session.execute(
            select(Organization.id).where(
                Organization.name.like(f"{P1P2_ORG_PREFIX}%")
            )
        )
        assert not leftover.all(), "Phase 1P.2 namespace leaked organizations"


@pytest_asyncio.fixture
async def org_scope(db):
    org_ids: list[int] = []

    async def make() -> Organization:
        org = Organization(name=f"{P1P2_ORG_PREFIX}{uuid.uuid4().hex[:10]}")
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        await db.commit()
        return org

    yield make

    if org_ids:
        for table_name in _PURGE_TABLES:
            table = Base.metadata.tables.get(table_name)
            if table is not None and "organization_id" in table.columns:
                await db.execute(
                    delete(table).where(table.c.organization_id.in_(org_ids))
                )
        await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
        await db.commit()


def _build_token(*, sub: str) -> str:
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "iss": os.environ["AUTH_JWT_ISSUER"],
        "aud": os.environ["AUTH_JWT_AUDIENCE"],
        "exp": now + 3600,
        "iat": now,
    }
    return jwt.encode(payload, os.environ["AUTH_JWT_SECRET"], algorithm="HS256")


async def _make_widget_key(db, org: Organization) -> str:
    key = f"pk_live_{secrets.token_hex(20)}"
    db.add(
        PublicChatConfiguration(
            organization_id=org.id,
            public_widget_key_hash=hash_digest(key),
            display_name="Acme Support",
            welcome_message="Hi!",
            allowed_origins=[ALLOWED_ORIGIN],
            enabled=True,
            max_message_length=4000,
            max_messages_per_minute=20,
            session_ttl_hours=24,
        )
    )
    await db.commit()
    return key


def _origin_headers(token: str | None = None) -> dict:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    headers[X_ORIGIN] = ALLOWED_ORIGIN
    return headers


async def _create_session(client, key: str) -> dict:
    response = await client.post(
        "/public/chat/sessions",
        json={"public_widget_key": key},
        headers=_origin_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _staff_headers(subject: str, organization_id: int) -> dict:
    return {
        "Authorization": f"Bearer {_build_token(sub=subject)}",
        TENANT_HEADER: str(organization_id),
    }


async def _make_widget_session(
    db,
    client,
    org: Organization,
    *,
    request_human: bool = True,
) -> dict:
    key = await _make_widget_key(db, org)
    created = await _create_session(client, key)
    token = created["session"]["token"]
    if request_human:
        await _request_human(client, token)
    return {"session": created["session"], "token": token}


async def _request_human(client, token: str) -> dict:
    response = await client.post(
        "/public/chat/sessions/human",
        headers=_origin_headers(token=token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _add_membership(db, subject: str, organization_id: int) -> None:
    db.add(
        OrganizationMembership(
            subject=subject,
            organization_id=organization_id,
            role=OrganizationRole.OWNER,
        )
    )
    await db.commit()


async def _make_enabled_run(
    db,
    org: Organization,
    ticket: Ticket,
    *,
    provider: str | None = PROVIDER_A1,
) -> AgentRun:
    if provider is not None:
        await BusinessIntegrationService.set_provider_enabled(
            db,
            org.id,
            provider,
            True,
        )
        await db.commit()
    run = AgentRun(
        run_id=uuid.uuid4().hex,
        ticket_id=ticket.id,
        organization_id=org.id,
        action="respond",
        reason="A customer-visible business action.",
        response_draft=None,
        reviewer_note=None,
        status="approved",
        sources=[],
        workflow_path=["load_ticket"],
        tool_plan=[],
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


_A1_LEAD_PLAN = [
    {
        "tool": "a1.create_vehicle_lead",
        "arguments": {
            "vehicle_make": "Ford",
            "vehicle_model": "F-150",
            "vehicle_year": 2020,
            "condition": "good",
        },
    }
]


# ----------------------------------------------------------------------
# Tenant enablement (fail-closed)
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enabled_tool_names_fails_closed(db, org_scope):
    org = await org_scope()

    default = await BusinessIntegrationService.enabled_tool_names(db, org.id)
    assert default == {"customer.send_reply"}

    await BusinessIntegrationService.set_provider_enabled(
        db, org.id, PROVIDER_A1, True
    )
    await db.commit()
    enabled = await BusinessIntegrationService.enabled_tool_names(db, org.id)
    assert "customer.send_reply" in enabled
    assert "a1.create_vehicle_lead" in enabled
    assert "a1.create_pickup_request" in enabled

    await BusinessIntegrationService.set_provider_enabled(
        db, org.id, PROVIDER_A1, False
    )
    await db.commit()
    disabled = await BusinessIntegrationService.enabled_tool_names(db, org.id)
    assert disabled == {"customer.send_reply"}


@pytest.mark.asyncio
async def test_execution_rechecks_enablement_and_fails_closed(db, org_scope):
    org = await org_scope()
    key = await _make_widget_key(db, org)
    created = await public_chat_service.create_session(
        db,
        public_widget_key=key,
        embedding_origin=ALLOWED_ORIGIN,
    )
    await db.commit()
    ticket = await db.get(Ticket, created["session"].ticket_id)

    run = await _make_enabled_run(db, org, ticket, provider=PROVIDER_A1)

    await BusinessIntegrationService.set_provider_enabled(
        db, org.id, PROVIDER_A1, False
    )
    await db.commit()

    with pytest.raises(BusinessActionPlanError):
        await business_action_service.execute(
            db,
            run=run,
            ticket=ticket,
            organization_id=org.id,
            tool_plan=_A1_LEAD_PLAN,
        )
    rows = await db.execute(
        select(ConversationMessage).where(
            ConversationMessage.organization_id == org.id
        )
    )
    assert not rows.all()


@pytest.mark.asyncio
async def test_unknown_provider_is_never_enabled(db, org_scope):
    org = await org_scope()
    enabled = await BusinessIntegrationService.enabled_tool_names(db, org.id)
    assert all(not name.startswith("a1.") for name in enabled)


# ----------------------------------------------------------------------
# Durable business action execution + idempotent mirror
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_business_action_executes_and_mirrors_idempotently(db, org_scope):
    org = await org_scope()
    key = await _make_widget_key(db, org)
    created = await public_chat_service.create_session(
        db,
        public_widget_key=key,
        embedding_origin=ALLOWED_ORIGIN,
    )
    await db.commit()
    ticket = await db.get(Ticket, created["session"].ticket_id)
    run = await _make_enabled_run(db, org, ticket)

    await business_action_service.execute(
        db,
        run=run,
        ticket=ticket,
        organization_id=org.id,
        tool_plan=_A1_LEAD_PLAN,
    )
    await business_action_service.execute(
        db,
        run=run,
        ticket=ticket,
        organization_id=org.id,
        tool_plan=_A1_LEAD_PLAN,
    )

    row = await BusinessActionRepository.find_by_dedupe(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
        request_type="a1.create_vehicle_lead",
        dedupe_key=f"agent_run:{run.run_id}:a1.create_vehicle_lead",
    )
    assert row is not None
    assert row.status == "completed"
    assert row.reference_id and row.reference_id.startswith("A1-")
    assert row.run_id == run.run_id
    for key, value in _A1_LEAD_PLAN[0]["arguments"].items():
        assert row.payload_json.get(key) == value
    assert row.request_type == "a1.create_vehicle_lead"

    messages = (
        await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.organization_id == org.id,
                ConversationMessage.dedupe_key
                == f"agent_run:{run.run_id}:a1.create_vehicle_lead",
            )
        )
    ).scalars().all()
    assert len(messages) == 1, "re-execution must not duplicate the mirror"
    (message,) = messages
    assert message.direction == "outbound"
    assert message.visibility == "public"
    assert message.provider == LOCAL_PROVIDER
    assert "vehicle listing request" in message.body.lower()
    assert "recommended_team" not in message.body.lower()


# ----------------------------------------------------------------------
# Forbidden tool-argument identity hardening
# ----------------------------------------------------------------------


@pytest.fixture
def _freeform_vehicle_lead_arguments():
    """Relax the A1 vehicle-lead tool to free-form arguments.

    Models a tool whose argument model permits arbitrary keys. Under this tool
    the authorization digest happily covers identity arguments, which is
    precisely why the preflight forbidden-argument guard must exist and why it
    is the only remaining defence.
    """
    policy = ToolAuthorizationService.POLICIES["a1.create_vehicle_lead"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(policy, "argument_schema", {})
        yield


async def _authorized_run_with_freeform_args(
    db,
    org: Organization,
    ticket: Ticket,
    *,
    arguments: dict,
) -> AgentRun:
    """Build a run whose persisted plan is *legitimately* authorized.

    Authorization and the canonical digest are both computed over the plan that
    carries ``arguments``, so the run is digest-valid and policy-valid. The
    only remaining gate is the preflight identity check.
    """
    run = await _make_enabled_run(db, org, ticket)
    authorized = ToolAuthorizationService.authorize_plan(
        [
            {
                "tool": "a1.create_vehicle_lead",
                "arguments": arguments,
            }
        ]
    )
    run.tool_plan = authorized
    run.tool_policy_version = ToolAuthorizationService.TOOL_POLICY_VERSION
    run.authorization_digest = ToolAuthorizationService.compute_run_digest(
        run_id=run.run_id,
        organization_id=org.id,
        ticket_id=ticket.id,
        policy_version=run.tool_policy_version,
        tool_plan=authorized,
    )
    await db.commit()
    await db.refresh(run)
    return run


@pytest.mark.parametrize(
    "identity_field",
    ["organization_id", "conversation_id", "customer_id"],
)
@pytest.mark.asyncio
async def test_forbidden_identity_argument_rejected_before_execution(
    db,
    org_scope,
    _freeform_vehicle_lead_arguments,
    identity_field,
):
    """Model-supplied identity arguments never reach a business tool.

    The digest is computed over the *tampered* plan, so the digest check
    passes. Only the preflight forbidden-argument guard can reject it, which
    proves that guard is load-bearing rather than a redundant second check.
    """
    org = await org_scope()
    key = await _make_widget_key(db, org)
    created = await public_chat_service.create_session(
        db,
        public_widget_key=key,
        embedding_origin=ALLOWED_ORIGIN,
    )
    await db.commit()
    ticket = await db.get(Ticket, created["session"].ticket_id)

    assert identity_field in FORBIDDEN_TOOL_ARGUMENT_IDENTITY_FIELDS

    run = await _authorized_run_with_freeform_args(
        db,
        org,
        ticket,
        arguments={"vehicle_make": "Ford", identity_field: 987654321},
    )

    # The plan is fully authorized and untampered from the digest's point of
    # view -- so the digest check alone would let it through.
    assert ToolAuthorizationService.validate_run_digest(run) is True

    with pytest.raises(AgentExecutionError) as excinfo:
        await AgentExecutionService()._validate_plan_preflight(run)

    assert identity_field in str(excinfo.value)
    assert "forbidden" in str(excinfo.value).lower()

    # Nothing executed: no durable business action and no outbound mirror.
    assert (
        await BusinessActionRepository.find_by_dedupe(
            db,
            organization_id=org.id,
            ticket_id=ticket.id,
            request_type="a1.create_vehicle_lead",
            dedupe_key=f"agent_run:{run.run_id}:a1.create_vehicle_lead",
        )
        is None
    )


# ----------------------------------------------------------------------
# Staff handoff API
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_staff_assign_release_flow(db, client, org_scope):
    org = await org_scope()
    await _make_widget_session(db, client, org)

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = await _staff_headers(subject, org.id)

    listing = await client.get("/staff/public-chat/handoff", headers=headers)
    assert listing.status_code == 200, listing.text
    sessions = listing.json()["sessions"]
    assert len(sessions) == 1
    session_id = sessions[0]["session_id"]
    assert sessions[0]["status"] == "human_requested"

    assigned = await client.post(
        f"/staff/public-chat/sessions/{session_id}/assign",
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "human_assigned"
    assert assigned.json()["assigned_to_subject"] == subject

    released = await client.post(
        f"/staff/public-chat/sessions/{session_id}/release",
        headers=headers,
    )
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "human_requested"


@pytest.mark.asyncio
async def test_staff_assign_rejects_wrong_state(db, client, org_scope):
    org = await org_scope()
    await _make_widget_session(db, client, org)

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = await _staff_headers(subject, org.id)

    listing = await client.get("/staff/public-chat/handoff", headers=headers)
    session_id = listing.json()["sessions"][0]["session_id"]

    first = await client.post(
        f"/staff/public-chat/sessions/{session_id}/release",
        headers=headers,
    )
    assert first.status_code == 409, first.text

    assigned = await client.post(
        f"/staff/public-chat/sessions/{session_id}/assign",
        headers=headers,
    )
    assert assigned.status_code == 200
    second_assign = await client.post(
        f"/staff/public-chat/sessions/{session_id}/assign",
        headers=headers,
    )
    assert second_assign.status_code == 409, second_assign.text


@pytest.mark.asyncio
async def test_staff_cannot_touch_foreign_tenant(db, client, org_scope):
    org_a = await org_scope()
    org_b = await org_scope()
    await _make_widget_session(db, client, org_a)

    subject_a = f"staff-a-{uuid.uuid4().hex[:8]}"
    subject_b = f"staff-b-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject_a, org_a.id)
    await _add_membership(db, subject_b, org_b.id)

    headers_a = await _staff_headers(subject_a, org_a.id)
    headers_b = await _staff_headers(subject_b, org_b.id)

    listing_a = await client.get("/staff/public-chat/handoff", headers=headers_a)
    sessions_a = listing_a.json()["sessions"]
    assert len(sessions_a) == 1
    session_id = sessions_a[0]["session_id"]

    listing_b = await client.get("/staff/public-chat/handoff", headers=headers_b)
    assert listing_b.json()["sessions"] == []

    rogue = await client.post(
        f"/staff/public-chat/sessions/{session_id}/assign",
        headers=headers_b,
    )
    assert rogue.status_code == 404, rogue.text

    actions = await client.get(
        f"/staff/public-chat/sessions/{session_id}/business-actions",
        headers=headers_b,
    )
    assert actions.status_code == 404, actions.text

    clean = await client.post(
        f"/staff/public-chat/sessions/{session_id}/assign",
        headers=headers_a,
    )
    assert clean.status_code == 200, clean.text


@pytest.mark.asyncio
async def test_staff_business_actions_listing_is_bounded(db, client, org_scope):
    org = await org_scope()
    key = await _make_widget_key(db, org)
    created = await public_chat_service.create_session(
        db,
        public_widget_key=key,
        embedding_origin=ALLOWED_ORIGIN,
    )
    await db.commit()
    ticket = await db.get(Ticket, created["session"].ticket_id)
    session_row = created["session"]

    await public_chat_service.request_human(
        db,
        await db.get(PublicChatSession, session_row.id),
    )
    await db.commit()

    run = await _make_enabled_run(db, org, ticket)
    await business_action_service.execute(
        db,
        run=run,
        ticket=ticket,
        organization_id=org.id,
        tool_plan=_A1_LEAD_PLAN,
    )

    subject = f"staff-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject, org.id)
    headers = await _staff_headers(subject, org.id)

    response = await client.get(
        f"/staff/public-chat/sessions/{session_row.id}/business-actions",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    actions = response.json()["actions"]
    assert len(actions) == 1
    action = actions[0]
    assert action["request_type"] == "a1.create_vehicle_lead"
    assert action["status"] == "completed"
    assert action["reference_id"].startswith("A1-")
    assert "arguments" not in action
    assert "tenant" not in str(action).lower()


# ----------------------------------------------------------------------
# DB rate limiter charging
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limiter_db_charges_only_accepted(db):
    scope = "test"
    key = f"session:{uuid.uuid4().hex}"
    limit = 3
    window = 60

    results = [
        await public_chat_rate_limiter_db.allow(
            db,
            scope=scope,
            key=key,
            limit=limit,
            window_seconds=window,
        )
        for _ in range(limit)
    ]
    assert results == [True, True, True]

    exceeded = await public_chat_rate_limiter_db.allow(
        db,
        scope=scope,
        key=key,
        limit=limit,
        window_seconds=window,
    )
    assert exceeded is False
    again = await public_chat_rate_limiter_db.allow(
        db,
        scope=scope,
        key=key,
        limit=limit,
        window_seconds=window,
    )
    assert again is False

    rows = await db.execute(
        select(PublicChatRateLimitBucket.count).where(
            PublicChatRateLimitBucket.key_cache == f"{scope}:{key}",
        )
    )
    assert [count for (count,) in rows.all()] == [limit]

    pruned = await public_chat_rate_limiter_db.prune(
        db,
        now_epoch=datetime.now(UTC).timestamp()
        + (2 * 3600 + 60),
    )
    assert pruned >= 1

# ----------------------------------------------------------------------
# Concurrent assignment safety (atomic tenant+status conditional UPDATE)
# ----------------------------------------------------------------------


async def _handoff_session_id(db, org: Organization) -> int:
    session_id = (
        await db.execute(
            select(PublicChatSession.id).where(
                PublicChatSession.organization_id == org.id
            )
        )
    ).scalar_one()
    return session_id


async def _reload_session(db, session_id: int) -> PublicChatSession:
    """Re-read the row from the database, bypassing the identity map."""
    await db.rollback()
    return (
        await db.execute(
            select(PublicChatSession).where(PublicChatSession.id == session_id)
        )
    ).scalar_one()


async def _assign_in_isolated_session(
    *, session_id: int, organization_id: int, subject: str
):
    """Assign through a dedicated DB session so concurrent calls really overlap."""
    async with AsyncSessionLocal() as session:
        try:
            return await PublicChatStaffService.assign(
                session,
                session_id=session_id,
                organization_id=organization_id,
                subject=subject,
            )
        except PublicChatStaffError as exc:
            return exc


async def _release_in_isolated_session(*, session_id: int, organization_id: int):
    async with AsyncSessionLocal() as session:
        try:
            return await PublicChatStaffService.release(
                session,
                session_id=session_id,
                organization_id=organization_id,
            )
        except PublicChatStaffError as exc:
            return exc


@pytest.mark.asyncio
async def test_concurrent_assign_admits_exactly_one_staff(db, client, org_scope):
    """Two simultaneous assigners: exactly one wins, the other gets a conflict."""
    org = await org_scope()
    await _make_widget_session(db, client, org)
    session_id = await _handoff_session_id(db, org)

    subject_a = f"staff-a-{uuid.uuid4().hex[:8]}"
    subject_b = f"staff-b-{uuid.uuid4().hex[:8]}"

    results = await asyncio.gather(
        _assign_in_isolated_session(
            session_id=session_id, organization_id=org.id, subject=subject_a
        ),
        _assign_in_isolated_session(
            session_id=session_id, organization_id=org.id, subject=subject_b
        ),
    )

    winners = [r for r in results if isinstance(r, dict)]
    conflicts = [r for r in results if isinstance(r, PublicChatStaffAssignmentError)]
    assert len(winners) == 1, results
    assert len(conflicts) == 1, results

    row = await _reload_session(db, session_id)
    assert row.status == "human_assigned"
    assert row.assigned_to_subject == winners[0]["assigned_to_subject"]
    assert row.assigned_to_subject in {subject_a, subject_b}
    assert row.assigned_at is not None


@pytest.mark.asyncio
async def test_concurrent_assign_over_http_returns_200_and_409(
    db, client, org_scope
):
    """The race surfaces at the API as exactly one 200 and one 409."""
    org = await org_scope()
    await _make_widget_session(db, client, org)
    session_id = await _handoff_session_id(db, org)

    subject_a = f"staff-a-{uuid.uuid4().hex[:8]}"
    subject_b = f"staff-b-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, subject_a, org.id)
    await _add_membership(db, subject_b, org.id)

    url = f"/staff/public-chat/sessions/{session_id}/assign"
    responses = await asyncio.gather(
        client.post(url, headers=await _staff_headers(subject_a, org.id)),
        client.post(url, headers=await _staff_headers(subject_b, org.id)),
    )

    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 409], [r.status_code for r in responses]

    row = await _reload_session(db, session_id)
    assert row.status == "human_assigned"
    assert row.assigned_to_subject in {subject_a, subject_b}


@pytest.mark.asyncio
async def test_concurrent_release_and_assign_yields_only_legal_transition(
    db, client, org_scope
):
    """A release/assign race may only land on a legal serial order.

    Starting from ``human_assigned`` the only legal serializations are
    "release, then possibly assign" or "release alone". The final row must
    never be torn: the status and the assignee must agree.
    """
    org = await org_scope()
    await _make_widget_session(db, client, org)
    session_id = await _handoff_session_id(db, org)

    holder = f"staff-holder-{uuid.uuid4().hex[:8]}"
    claimer = f"staff-claimer-{uuid.uuid4().hex[:8]}"
    await PublicChatStaffService.assign(
        db, session_id=session_id, organization_id=org.id, subject=holder
    )
    await db.commit()
    assert (await _reload_session(db, session_id)).status == "human_assigned"

    release_result, assign_result = await asyncio.gather(
        _release_in_isolated_session(
            session_id=session_id, organization_id=org.id
        ),
        _assign_in_isolated_session(
            session_id=session_id, organization_id=org.id, subject=claimer
        ),
    )

    # The release is legal from human_assigned, so it must always win.
    assert isinstance(release_result, dict), release_result

    row = await _reload_session(db, session_id)
    if isinstance(assign_result, dict):
        # The claimer only succeeds if it observed the post-release state.
        assert assign_result["assigned_to_subject"] == claimer
        assert row.status == "human_assigned"
        assert row.assigned_to_subject == claimer
        assert row.assigned_at is not None
    else:
        assert isinstance(assign_result, PublicChatStaffAssignmentError)
        assert row.status == "human_requested"
        assert row.assigned_to_subject is None
        assert row.assigned_at is None

    # Never the holder: the release always cleared the assignment.
    assert row.assigned_to_subject != holder


@pytest.mark.asyncio
async def test_concurrent_double_release_admits_exactly_one(
    db, client, org_scope
):
    """Two simultaneous releases: exactly one succeeds."""
    org = await org_scope()
    await _make_widget_session(db, client, org)
    session_id = await _handoff_session_id(db, org)

    holder = f"staff-holder-{uuid.uuid4().hex[:8]}"
    await PublicChatStaffService.assign(
        db, session_id=session_id, organization_id=org.id, subject=holder
    )
    await db.commit()

    results = await asyncio.gather(
        _release_in_isolated_session(
            session_id=session_id, organization_id=org.id
        ),
        _release_in_isolated_session(
            session_id=session_id, organization_id=org.id
        ),
    )

    winners = [r for r in results if isinstance(r, dict)]
    conflicts = [r for r in results if isinstance(r, PublicChatStaffAssignmentError)]
    assert len(winners) == 1, results
    assert len(conflicts) == 1, results

    row = await _reload_session(db, session_id)
    assert row.status == "human_requested"
    assert row.assigned_to_subject is None


@pytest.mark.asyncio
async def test_concurrent_assign_on_requested_session_rejects_release_legally(
    db, client, org_scope
):
    """From human_requested, a concurrent release is a 409 conflict, not a success."""
    org = await org_scope()
    await _make_widget_session(db, client, org)
    session_id = await _handoff_session_id(db, org)

    claimer = f"staff-claimer-{uuid.uuid4().hex[:8]}"
    release_result, assign_result = await asyncio.gather(
        _release_in_isolated_session(
            session_id=session_id, organization_id=org.id
        ),
        _assign_in_isolated_session(
            session_id=session_id, organization_id=org.id, subject=claimer
        ),
    )

    assert isinstance(release_result, PublicChatStaffAssignmentError)
    assert isinstance(assign_result, dict)
    assert assign_result["assigned_to_subject"] == claimer

    row = await _reload_session(db, session_id)
    assert row.status == "human_assigned"
    assert row.assigned_to_subject == claimer


@pytest.mark.asyncio
async def test_concurrent_foreign_tenant_assign_stays_not_found(
    db, client, org_scope
):
    """A foreign tenant's lost race is 404, never 409 -- no enumeration leak."""
    owner = await org_scope()
    await _make_widget_session(db, client, owner)
    session_id = await _handoff_session_id(db, owner)

    intruder_org = await org_scope()
    intruder = f"staff-intruder-{uuid.uuid4().hex[:8]}"
    await _add_membership(db, intruder, intruder_org.id)
    headers = await _staff_headers(intruder, intruder_org.id)

    url = f"/staff/public-chat/sessions/{session_id}/assign"
    responses = await asyncio.gather(
        client.post(url, headers=headers),
        client.post(url, headers=headers),
    )
    assert [r.status_code for r in responses] == [404, 404]

    service_results = await asyncio.gather(
        _assign_in_isolated_session(
            session_id=session_id, organization_id=intruder_org.id, subject=intruder
        ),
        _assign_in_isolated_session(
            session_id=session_id, organization_id=intruder_org.id, subject=intruder
        ),
    )
    for outcome in service_results:
        assert isinstance(outcome, PublicChatStaffSessionNotFoundError)

    # The owning tenant's row is untouched.
    row = await _reload_session(db, session_id)
    assert row.status == "human_requested"
    assert row.assigned_to_subject is None
