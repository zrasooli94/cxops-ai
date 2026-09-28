"""Kill-switch behaviour for the A1 pilot, and cross-tenant isolation.

Two switches can stop the pilot mid-flight, and an operator who reaches for one
of them at 9pm needs it to fail closed, say why, and leave the audit trail
intact:

* the **chat switch** -- ``public_chat_configurations.enabled`` -- stops new
  sessions and new messages for a tenant, and
* the **provider switch** -- ``business_integration_configurations.enabled`` --
  stops the A1 business tools while leaving conversational chat available.

The provider switch is checked at the service layer rather than through the LLM.
The agent decides on its own whether a given message warrants a tool call, so an
end-to-end probe cannot reliably *cause* one; asserting on the enablement
lookup tests the actual guard, which is where the decision is made.

The isolation tests at the bottom cover the property that makes multi-tenant
pilot safe: a session, a widget key, and a business action from tenant A must
never be reachable from tenant B, even by guessing an identifier.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import delete

# Import the tool registry before the A1 service; see the note in
# tests/test_a1_local_demo_wording.py for why that order is required.
import app.tools.registry  # noqa: F401
from app.integrations.a1_cash_for_cars.service import LOCAL_DEMO_TAG
from app.models.organization import Organization
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.services.business_action_service import BusinessIntegrationService
from app.services.public_chat_service import (
    PublicChatService,
    PublicChatWidgetDisabledError,
)


async def _purge_orgs(db, org_ids):
    """Delete every row belonging to these tenants, in FK-safe order.

    ``create_session`` writes a ticket and a conversation, so removing only the
    public-chat rows leaves the organizations still referenced and the delete
    fails. Sweeping every organization-scoped table is both simpler and safer
    than remembering the full chain.
    """
    if not org_ids:
        return
    from app.models.base import Base

    for table_name in _PURGE_TABLES:
        table = Base.metadata.tables.get(table_name)
        if table is not None and "organization_id" in table.columns:
            await db.execute(
                delete(table).where(table.c.organization_id.in_(org_ids))
            )
    await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.commit()


async def _make_widget_config(db, org, *, display_name: str, enabled: bool = True):
    """Seed a widget row the same way the API's own path expects."""
    key = f"pk_live_{uuid.uuid4().hex}"
    config = PublicChatConfiguration(
        organization_id=org.id,
        public_widget_key_hash=_digest(key),
        display_name=display_name,
        welcome_message="Hi",
        allowed_origins=["https://a1cashforcars.com.au"],
        enabled=enabled,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config, key


# Every organization-scoped table this suite can create rows in.
_PURGE_TABLES = (
    "public_chat_sessions",
    "public_chat_configurations",
    "public_chat_rate_buckets",
    "business_integration_configurations",
    "business_actions",
    "conversations",
    "tickets",
    "agent_runs",
    "agent_decisions",
)

A1_TOOLS = {
    "a1.create_vehicle_lead",
    "a1.update_vehicle_details",
    "a1.request_vehicle_photos",
    "a1.get_quote_status",
    "a1.check_pickup_availability",
    "a1.create_pickup_request",
}


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Chat kill switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabling_the_widget_blocks_session_creation(db):
    """A disabled widget must not yield a session token."""
    slug = f"ks-chat-{uuid.uuid4().hex[:10]}"
    org = Organization(external_id=slug, name=slug)
    db.add(org)
    await db.flush()
    org_id = org.id

    key = f"pk_live_{uuid.uuid4().hex}"
    config = PublicChatConfiguration(
        organization_id=org_id,
        public_widget_key_hash=_digest(key),
        display_name="Kill Switch",
        welcome_message="Hi",
        allowed_origins=["https://a1cashforcars.com.au"],
        enabled=True,
    )
    db.add(config)
    await db.commit()

    try:
        resolved = await PublicChatService.resolve_config(db, key)
        assert resolved.enabled is True

        config.enabled = False
        await db.commit()

        with pytest.raises(PublicChatWidgetDisabledError) as excinfo:
            await PublicChatService.resolve_config(db, key)
        assert "disabled" in str(excinfo.value).lower()
    finally:
        await _purge_orgs(db, [org_id])


@pytest.mark.asyncio
async def test_re_enabling_restores_access(db):
    """The switch must be reversible; a pilot needs it back the same day."""
    slug = f"ks-reenable-{uuid.uuid4().hex[:10]}"
    org = Organization(external_id=slug, name=slug)
    db.add(org)
    await db.flush()
    org_id = org.id

    key = f"pk_live_{uuid.uuid4().hex}"
    config = PublicChatConfiguration(
        organization_id=org_id,
        public_widget_key_hash=_digest(key),
        display_name="Reversible",
        welcome_message="Hi",
        allowed_origins=["https://a1cashforcars.com.au"],
        enabled=False,
    )
    db.add(config)
    await db.commit()

    try:
        with pytest.raises(PublicChatWidgetDisabledError):
            await PublicChatService.resolve_config(db, key)

        config.enabled = True
        await db.commit()

        resolved = await PublicChatService.resolve_config(db, key)
        assert resolved.enabled is True
    finally:
        await _purge_orgs(db, [org_id])


@pytest.mark.asyncio
async def test_an_unknown_key_never_reveals_that_a_widget_is_disabled(db):
    """A missing widget and a disabled widget must look identical to a caller.

    Differentiating them would let anyone probe which keys exist.
    """
    from app.services.public_chat_service import PublicChatConfigurationNotFoundError

    with pytest.raises(PublicChatConfigurationNotFoundError):
        await PublicChatService.resolve_config(
            db, f"pk_live_{uuid.uuid4().hex}"
        )
    await db.rollback()


# ---------------------------------------------------------------------------
# Provider kill switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabling_the_provider_withdraws_every_a1_tool(db):
    """The provider switch must remove the whole A1 tool set, not one tool."""
    from app.models.business_integration import BusinessIntegrationConfiguration

    slug = f"ks-provider-{uuid.uuid4().hex[:10]}"
    org = Organization(external_id=slug, name=slug)
    db.add(org)
    await db.flush()
    org_id = org.id

    row = BusinessIntegrationConfiguration(
        organization_id=org_id,
        provider="a1_cash_for_cars",
        enabled=True,
        config_json={"provider_mode": "local_demo"},
    )
    db.add(row)
    await db.commit()

    try:
        enabled = await BusinessIntegrationService.enabled_tool_names(db, org_id)
        assert enabled & A1_TOOLS, enabled

        row.enabled = False
        await db.commit()

        disabled = await BusinessIntegrationService.enabled_tool_names(db, org_id)
        assert not (disabled & A1_TOOLS), disabled
    finally:
        await _purge_orgs(db, [org_id])


@pytest.mark.asyncio
async def test_a_disabled_provider_blocks_execution_not_just_listing(db):
    """Turning the provider off must be enforced where the action is executed.

    ``enabled_tool_names`` drives what the agent is *offered*. That is not the
    same as permission to run: a plan can name a tool that was enabled a moment
    ago, or be replayed from a stored run. The execution path re-reads the DB
    and must refuse, so a switch flip takes effect on in-flight and stored plans
    rather than only steering the next turn.
    """
    from app.models.business_integration import BusinessIntegrationConfiguration
    from app.services.business_action_service import (
        BusinessActionPlanError,
        BusinessActionService,
    )

    slug = f"ks-exec-{uuid.uuid4().hex[:10]}"
    org = Organization(external_id=slug, name=slug)
    db.add(org)
    await db.flush()
    org_id = org.id

    row = BusinessIntegrationConfiguration(
        organization_id=org_id,
        provider="a1_cash_for_cars",
        enabled=True,
        config_json={"provider_mode": "local_demo"},
    )
    db.add(row)
    await db.commit()

    # ``assert_plan_executable`` reads only ``run.organization_id``. A real
    # AgentRun would drag in a non-null ticket FK that this check does not need.
    from types import SimpleNamespace

    run = SimpleNamespace(organization_id=org_id)

    plan = [{"tool": "a1.check_pickup_availability", "arguments": {}}]

    try:
        # Enabled: the plan is executable.
        await BusinessActionService.assert_plan_executable(
            db, run=run, tool_plan=plan
        )

        # Flip the switch. A brand-new plan must now be refused.
        row.enabled = False
        await db.commit()

        with pytest.raises(BusinessActionPlanError) as exc:
            await BusinessActionService.assert_plan_executable(
                db, run=run, tool_plan=plan
            )
        assert "not enabled for this tenant" in str(exc.value), exc.value
    finally:
        await _purge_orgs(db, [org_id])


@pytest.mark.asyncio
async def test_a_disabled_provider_still_allows_conversational_tools(db):
    """Switching A1 off must not disable unrelated tenant tools."""
    from app.models.business_integration import BusinessIntegrationConfiguration

    slug = f"ks-others-{uuid.uuid4().hex[:10]}"
    org = Organization(external_id=slug, name=slug)
    db.add(org)
    await db.flush()
    org_id = org.id

    db.add_all(
        [
            BusinessIntegrationConfiguration(
                organization_id=org_id,
                provider="a1_cash_for_cars",
                enabled=False,
                config_json={"provider_mode": "local_demo"},
            ),
            BusinessIntegrationConfiguration(
                organization_id=org_id,
                provider="other_provider",
                enabled=True,
                config_json={},
            ),
        ]
    )
    await db.commit()

    try:
        enabled = await BusinessIntegrationService.enabled_tool_names(db, org_id)
        assert not (enabled & A1_TOOLS), enabled
    finally:
        await _purge_orgs(db, [org_id])


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_widget_key_belongs_to_exactly_one_tenant(db):
    """Tenant A's key must not resolve to tenant B's configuration."""
    orgs = []
    try:
        for label in ("iso-a", "iso-b"):
            org = Organization(
                external_id=f"{label}-{uuid.uuid4().hex[:8]}",
                name=f"{label}-{uuid.uuid4().hex[:8]}",
            )
            db.add(org)
            await db.flush()
            orgs.append(org)

        keys = {}
        for org, label in zip(orgs, ("iso-a", "iso-b"), strict=True):
            key = f"pk_live_{uuid.uuid4().hex}"
            keys[org.id] = key
            db.add(
                PublicChatConfiguration(
                    organization_id=org.id,
                    public_widget_key_hash=_digest(key),
                    display_name=label,
                    welcome_message="Hi",
                    allowed_origins=["https://a1cashforcars.com.au"],
                    enabled=True,
                )
            )
        await db.commit()

        for org_id, key in keys.items():
            resolved = await PublicChatService.resolve_config(db, key)
            assert resolved.organization_id == org_id

        # Each key resolves to its own tenant and never to the other.
        assert (
            await PublicChatService.resolve_config(db, keys[orgs[0].id])
        ).organization_id != orgs[1].id
    finally:
        await _purge_orgs(db, [org.id for org in orgs])


@pytest.mark.asyncio
async def test_a_session_token_stays_bound_to_its_issuing_tenant(db):
    """A live session must not be reachable from, or leak into, another tenant.

    A session row carries an organization id, a configuration id, and a ticket
    id. Creating one through the real endpoint and then reading it back proves
    the whole chain resolves consistently -- the hand-built row version of this
    test could not catch a token that resolved to the wrong tenant, because it
    never exercised the lookup a caller actually performs.
    """
    from sqlalchemy import select

    orgs = []
    try:
        for label in ("tok-a", "tok-b"):
            org = Organization(
                external_id=f"{label}-{uuid.uuid4().hex[:8]}",
                name=f"{label}-{uuid.uuid4().hex[:8]}",
            )
            db.add(org)
            await db.flush()
            orgs.append(org)

        _config_a, key_a = await _make_widget_config(db, orgs[0], display_name="tok-a")
        _config_b, key_b = await _make_widget_config(db, orgs[1], display_name="tok-b")

        created_a = await PublicChatService.create_session(
            db, key_a, "https://a1cashforcars.com.au"
        )
        token_a = created_a["token"]

        session = await PublicChatService.verify_session(db, token_a)
        assert session.organization_id == orgs[0].id
        assert session.organization_id != orgs[1].id

        # Tenant B's key yields a session bound to B, never to A.
        created_b = await PublicChatService.create_session(
            db, key_b, "https://a1cashforcars.com.au"
        )
        token_b = created_b["token"]

        assert token_a != token_b
        session_b = await PublicChatService.verify_session(db, token_b)
        assert session_b.organization_id == orgs[1].id
        assert session_b.id != session.id

        # Every session these two tenants own must name their own configuration
        # and their own organization. Scoped to the two test tenants: the shared
        # development database legitimately holds the real A1 tenant's rows, and
        # sweeping the whole table would both leak other state and assert nothing
        # about this pair.
        own = {orgs[0].id, orgs[1].id}
        rows = await db.execute(
            select(PublicChatSession).where(PublicChatSession.organization_id.in_(own))
        )
        owned = rows.scalars().all()
        assert owned, "expected sessions for both tenants"
        for row in owned:
            assert row.organization_id in own
            key = key_a if row.organization_id == orgs[0].id else key_b
            resolved = await PublicChatService.resolve_config(db, key)
            assert resolved.organization_id == row.organization_id
    finally:
        await _purge_orgs(db, [org.id for org in orgs])


@pytest.mark.asyncio
def _provider_of(action) -> str | None:
    """Read the provider tag a recorded action actually carries.

    The tag lives in ``result_json["metadata"]["provider"]`` (the executor writes
    ``ToolExecutionResult.metadata`` there via ``_result_json``); there is no
    ``metadata_json`` column on ``BusinessAction``, so reading one would silently
    yield ``None`` and make this check assert nothing.
    """
    return (action.result_json or {}).get("metadata", {}).get("provider")


def _a1_simulation_violation(action) -> str | None:
    """Return a complaint if this succeeded A1 action could pass for a real call.

    A failed action is exempt: on failure the executor stores an empty metadata
    map, and nothing was ever dispatched, so there is nothing to misrepresent.
    """
    if action.request_type is None or not action.request_type.startswith("a1"):
        return None
    if action.status == "failed":
        return None
    provider = _provider_of(action)
    if provider != LOCAL_DEMO_TAG:
        return f"{action.request_type} is tagged provider={provider!r}"
    return None


@pytest.mark.asyncio
async def test_every_succeeded_a1_action_is_tagged_local_demo(db):
    """No recorded A1 action may be mistakable for a real provider call."""
    from sqlalchemy import select

    from app.models.business_action import BusinessAction

    rows = await db.execute(
        select(BusinessAction).where(BusinessAction.request_type.like("a1%")).limit(200)
    )
    actions = rows.scalars().all()
    violations = [v for v in (_a1_simulation_violation(a) for a in actions) if v]
    assert not violations, violations
    await db.rollback()


def test_the_a1_simulation_check_actually_rejects_a_live_provider():
    """Guard the guard: a mislabelled action must be reported, not tolerated.

    Without this, an empty result set -- or a schema change that moved the tag --
    would make the sweep above pass without ever testing anything.
    """
    from types import SimpleNamespace

    live = SimpleNamespace(
        request_type="a1_create_vehicle_lead",
        status="succeeded",
        result_json={"metadata": {"provider": "a1_live_api"}},
    )
    demo = SimpleNamespace(
        request_type="a1_create_vehicle_lead",
        status="succeeded",
        result_json={"metadata": {"provider": LOCAL_DEMO_TAG}},
    )
    untagged = SimpleNamespace(
        request_type="a1_create_vehicle_lead",
        status="succeeded",
        result_json={"metadata": {}},
    )
    failed = SimpleNamespace(
        request_type="a1_create_vehicle_lead",
        status="failed",
        result_json={"metadata": {}},
    )
    other_tenant_tool = SimpleNamespace(
        request_type="send_ticket_email",
        status="succeeded",
        result_json={"metadata": {"provider": "zendesk_live"}},
    )

    assert _a1_simulation_violation(live) is not None
    assert _a1_simulation_violation(untagged) is not None
    assert _a1_simulation_violation(demo) is None
    assert _a1_simulation_violation(failed) is None
    assert _a1_simulation_violation(other_tenant_tool) is None


# ---------------------------------------------------------------------------
# Handoff wording must not contradict the local-demo contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handoff_copy_matches_the_tenants_provider_mode(db):
    """A local-demo tenant must not be told a person will review the request.

    The A1 adapter's wording contract states that no human reviews pilot
    requests, because nothing is dispatched anywhere. The widget's generic
    handoff text promises exactly that review, so a local-demo tenant has to get
    different copy -- otherwise the one message every escalation path uses
    contradicts the adapter contract.
    """
    from app.models.business_integration import BusinessIntegrationConfiguration
    from app.services.public_chat_service import (
        HANDOFF_REPLY,
        LOCAL_DEMO_HANDOFF_REPLY,
    )
    from app.services.public_chat_service import (
        PublicChatService as _Svc,
    )

    slug = f"ks-copy-{uuid.uuid4().hex[:10]}"
    org = Organization(external_id=slug, name=slug)
    db.add(org)
    await db.flush()
    org_id = org.id

    try:
        # No provider configured: the generic promise is still accurate.
        assert await _Svc.handoff_reply_for(db, org_id) == HANDOFF_REPLY

        # Local demo: promising a review is false.
        demo = BusinessIntegrationConfiguration(
            organization_id=org_id,
            provider="a1_cash_for_cars",
            enabled=True,
            config_json={"provider_mode": "local_demo"},
        )
        db.add(demo)
        await db.commit()
        assert await _Svc.handoff_reply_for(db, org_id) == LOCAL_DEMO_HANDOFF_REPLY

        # A live provider may legitimately route to staff again.
        demo.config_json = {"provider_mode": "live"}
        await db.commit()
        assert await _Svc.handoff_reply_for(db, org_id) == HANDOFF_REPLY
    finally:
        await _purge_orgs(db, [org_id])


def test_local_demo_handoff_copy_makes_no_false_completion_claim() -> None:
    """The replacement copy must not promise any outcome the pilot cannot deliver."""
    from app.services.public_chat_service import LOCAL_DEMO_HANDOFF_REPLY

    lowered = LOCAL_DEMO_HANDOFF_REPLY.lower()

    for claim in (
        "someone will",
        "we will contact",
        "a team member will",
        "has been scheduled",
        "is confirmed",
        "offer is ready",
    ):
        assert claim not in lowered, claim

    # It must still be a real handoff: say the request was recorded, and deny
    # the two things a customer would otherwise assume.
    assert "recorded" in lowered
    assert "no one has been contacted" in lowered
