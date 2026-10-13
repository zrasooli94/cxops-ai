"""Phase 1Q-R closeout - staff AI spend is admitted through reservations.

The first hardening pass gated staff AI calls with a read-only check of
recorded spend. Concurrent requests near a limit all saw the same remaining
budget and all passed. These tests reproduce that race, then prove the
reservation-based admission (same ledger and advisory lock as public-chat
turns) admits only what fits - per tenant and platform-wide - and releases
reservations on success, failure and cancellation.
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
os.environ["AUTH_JWT_SECRET"] = "b" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import AuthorizationContext, OrganizationRole
from app.main import app
from app.models.ai_request_log import AIRequestLog
from app.models.ai_usage_reservation import AIUsageReservation
from app.models.base import Base
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.public_chat import PublicChatConfiguration
from app.services import tenant_ai_allowance_service as allowance_module
from app.services.embedding_service import embedding_service
from app.services.public_chat_service import hash_digest
from app.services.tenant_ai_allowance_service import (
    STAFF_KEY_PREFIX,
    TenantAIAllowanceService,
    month_start,
)

ORG_PREFIX = "Phase1QR-budget-"
ISS = "budget-race"
AUD = "cxops-unit-test"
TENANT = "x-cxops-organization-id"


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    for key, value in {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "b" * 32,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": ISS,
        "AUTH_JWT_AUDIENCE": AUD,
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
        "STAFF_AI_RESERVED_COST_USD": "0.02",
    }.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture(scope="module", autouse=True)
def _isolated():
    asyncio.run(_purge())
    yield
    asyncio.run(_purge())


async def _purge() -> None:
    async with AsyncSessionLocal() as session:
        ids = [
            i
            for (i,) in await session.execute(
                select(Organization.id).where(Organization.name.like(f"{ORG_PREFIX}%"))
            )
        ]
        if ids:
            for name in (
                "ai_usage_reservations",
                "ai_request_logs",
                "knowledge_chunks",
                "knowledge_documents",
                "public_chat_configurations",
                "organization_memberships",
            ):
                table = Base.metadata.tables[name]
                await session.execute(delete(table).where(table.c.organization_id.in_(ids)))
            await session.execute(delete(Organization).where(Organization.id.in_(ids)))
        await session.commit()


def _auth(sub: str, org_id: int) -> dict:
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {"sub": sub, "iss": ISS, "aud": AUD, "iat": now, "exp": now + 3600},
        "b" * 32,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}", TENANT: str(org_id)}


async def _tenant(db, *, budget: float, spent: float) -> tuple[int, str]:
    org = Organization(name=f"{ORG_PREFIX}{uuid.uuid4().hex[:8]}", lifecycle_status="active")
    db.add(org)
    await db.flush()
    sub = f"budget-{uuid.uuid4().hex[:10]}"
    db.add(OrganizationMembership(subject=sub, organization_id=org.id, role="owner"))
    db.add(
        PublicChatConfiguration(
            organization_id=org.id,
            public_widget_key_hash=hash_digest(f"pk_live_{secrets.token_hex(20)}"),
            display_name="Budget",
            welcome_message="Hi",
            allowed_origins=[],
            enabled=False,
            monthly_ai_budget_usd=budget,
        )
    )
    if spent:
        db.add(
            AIRequestLog(
                organization_id=org.id,
                request_id=uuid.uuid4().hex,
                feature="agent_decision",
                model="fake",
                status="success",
                question="q",
                estimated_cost_usd=spent,
                latency_ms=1.0,
            )
        )
    await db.commit()
    return org.id, sub


async def _open_staff_reservations(org_id: int) -> int:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AIUsageReservation).where(
                    AIUsageReservation.organization_id == org_id,
                    AIUsageReservation.reservation_key.like(f"{STAFF_KEY_PREFIX}%"),
                    AIUsageReservation.status == "reserved",
                )
            )
        ).scalars()
        return len(list(rows))


@pytest.fixture
def blocking_embeddings(monkeypatch):
    """Embeddings that wait for a release signal, so admitted requests stay in
    flight while the others try to get in. Never calls a provider."""
    gate = asyncio.Event()
    entered = []

    async def _embed_text(text: str) -> list[float]:
        entered.append(text)
        await gate.wait()
        return [0.0] * 1536

    monkeypatch.setattr(embedding_service, "embed_text", _embed_text)
    return gate, entered


# ----------------------------------------------------------------------
# The race, reproduced
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_check_admits_every_concurrent_request(db):
    """What the first gate did: every concurrent caller sees the same
    remaining budget, so ten callers are admitted where only two fit."""
    org_id, _sub = await _tenant(db, budget=5.0, spent=4.95)

    async def check():
        async with AsyncSessionLocal() as session:
            decision = await TenantAIAllowanceService.check(
                session, organization_id=org_id, configuration=None
            )
            return decision.allowed

    results = await asyncio.gather(*(check() for _ in range(10)))
    assert all(results)  # 10 admissions, budget room for 2 x $0.02


@pytest.mark.asyncio
async def test_concurrent_reservations_admit_only_what_fits(db):
    org_id, _sub = await _tenant(db, budget=5.0, spent=4.95)

    async def reserve():
        async with AsyncSessionLocal() as session:
            config = (
                await session.execute(
                    select(PublicChatConfiguration).where(
                        PublicChatConfiguration.organization_id == org_id
                    )
                )
            ).scalar_one()
            decision = await TenantAIAllowanceService.reserve(
                session,
                organization_id=org_id,
                configuration=config,
                key=f"{STAFF_KEY_PREFIX}{org_id}:{uuid.uuid4().hex}",
                estimate_usd=0.02,
                count_as_turn=False,
            )
            return decision.allowed

    results = await asyncio.gather(*(reserve() for _ in range(10)))
    assert results.count(True) == 2  # 4.95 + 2 x 0.02 <= 5.00 < 4.95 + 3 x 0.02


# ----------------------------------------------------------------------
# Through the HTTP API (the real dependency)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_staff_requests_cannot_overspend_tenant_budget(db, blocking_embeddings):
    gate, entered = blocking_embeddings
    org_id, sub = await _tenant(db, budget=5.0, spent=4.95)

    async def search():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return (
                await c.post("/knowledge/search", json={"query": "hours"}, headers=_auth(sub, org_id))
            ).status_code

    tasks = [asyncio.create_task(search()) for _ in range(8)]
    for _ in range(100):
        if len(entered) >= 2 and sum(t.done() for t in tasks) >= 6:
            break
        await asyncio.sleep(0.05)
    # While two requests are in flight, every other one was refused.
    assert len(entered) == 2
    assert await _open_staff_reservations(org_id) == 2
    gate.set()
    codes = await asyncio.gather(*tasks)
    assert codes.count(429) == 6
    assert codes.count(200) == 2
    # Both admitted reservations were released when their requests ended.
    assert await _open_staff_reservations(org_id) == 0


@pytest.mark.asyncio
async def test_platform_ceiling_is_shared_across_tenants(db, blocking_embeddings, monkeypatch):
    gate, entered = blocking_embeddings
    a_id, a_sub = await _tenant(db, budget=100.0, spent=0.0)
    b_id, b_sub = await _tenant(db, budget=100.0, spent=0.0)
    async with AsyncSessionLocal() as session:
        platform = await TenantAIAllowanceService._usage(
            session,
            organization_id=None,
            period_start=month_start(),
            now=datetime.now(UTC),
        )
    # Room for exactly three staff reservations platform-wide.
    monkeypatch.setattr(
        allowance_module.settings,
        "platform_monthly_ai_budget_usd",
        platform.spent_usd + platform.outstanding_usd + 0.065,
    )

    async def search(sub, org_id):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return (
                await c.post("/knowledge/search", json={"query": "x"}, headers=_auth(sub, org_id))
            ).status_code

    tasks = [
        asyncio.create_task(search(a_sub, a_id) if i % 2 == 0 else search(b_sub, b_id))
        for i in range(8)
    ]
    for _ in range(100):
        if len(entered) >= 3 and sum(t.done() for t in tasks) >= 5:
            break
        await asyncio.sleep(0.05)
    assert len(entered) == 3
    gate.set()
    codes = await asyncio.gather(*tasks)
    assert codes.count(200) == 3 and codes.count(429) == 5


@pytest.mark.asyncio
async def test_failed_call_releases_its_reservation(db, monkeypatch):
    org_id, sub = await _tenant(db, budget=5.0, spent=0.0)

    async def _boom(_text):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(embedding_service, "embed_text", _boom)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.post("/knowledge/search", json={"query": "x"}, headers=_auth(sub, org_id))
    assert r.status_code == 500
    assert await _open_staff_reservations(org_id) == 0
    async with AsyncSessionLocal() as session:
        settled = (
            await session.execute(
                select(AIUsageReservation.status).where(
                    AIUsageReservation.organization_id == org_id
                )
            )
        ).scalars().all()
    assert settled == ["settled"]


@pytest.mark.asyncio
async def test_cancelled_request_releases_its_reservation(db):
    from app.api.deps import StaffAIBudget

    org_id, sub = await _tenant(db, budget=5.0, spent=0.0)
    authz = AuthorizationContext(organization_id=org_id, subject=sub, role=OrganizationRole.OWNER)
    generator = StaffAIBudget()(authz)
    await generator.__anext__()  # admitted and reserved
    assert await _open_staff_reservations(org_id) == 1
    with pytest.raises(asyncio.CancelledError):
        await generator.athrow(asyncio.CancelledError())  # client went away
    assert await _open_staff_reservations(org_id) == 0


# ----------------------------------------------------------------------
# Accounting invariants
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_reservations_never_count_as_public_chat_turns(db):
    org_id, _sub = await _tenant(db, budget=5.0, spent=0.0)
    async with AsyncSessionLocal() as session:
        config = (
            await session.execute(
                select(PublicChatConfiguration).where(
                    PublicChatConfiguration.organization_id == org_id
                )
            )
        ).scalar_one()
        config.monthly_ai_turn_limit = 1
        await session.commit()
        for _ in range(3):
            staff = await TenantAIAllowanceService.reserve(
                session,
                organization_id=org_id,
                configuration=config,
                key=f"{STAFF_KEY_PREFIX}{org_id}:{uuid.uuid4().hex}",
                estimate_usd=0.02,
                count_as_turn=False,
            )
            assert staff.allowed  # the turn limit does not apply to staff calls
        report = await TenantAIAllowanceService.check(
            session, organization_id=org_id, configuration=config
        )
        assert report.turns_used == 0
        # The single public-chat turn is still available...
        turn = await TenantAIAllowanceService.reserve(
            session, organization_id=org_id, configuration=config, key=f"ai-turn:{uuid.uuid4().hex}"
        )
        assert turn.allowed
        # ...and the second is refused by the turn limit, as before.
        second = await TenantAIAllowanceService.reserve(
            session, organization_id=org_id, configuration=config, key=f"ai-turn:{uuid.uuid4().hex}"
        )
        assert not second.allowed and second.reason == "turn_limit_reached"
        # Staff reservations do count as money outstanding.
        assert report.outstanding_usd == pytest.approx(0.06)


@pytest.mark.asyncio
async def test_month_boundary_keeps_ledgers_separate(db):
    org_id, _sub = await _tenant(db, budget=0.03, spent=0.0)
    end_of_month = datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)
    next_month = datetime(2026, 11, 1, 0, 0, 1, tzinfo=UTC)
    async with AsyncSessionLocal() as session:
        config = (
            await session.execute(
                select(PublicChatConfiguration).where(
                    PublicChatConfiguration.organization_id == org_id
                )
            )
        ).scalar_one()
        key = f"{STAFF_KEY_PREFIX}{org_id}:{uuid.uuid4().hex}"
        october = await TenantAIAllowanceService.reserve(
            session, organization_id=org_id, configuration=config, key=key,
            now=end_of_month, estimate_usd=0.02, count_as_turn=False,
        )
        assert october.allowed
        full = await TenantAIAllowanceService.reserve(
            session, organization_id=org_id, configuration=config,
            key=f"{STAFF_KEY_PREFIX}{org_id}:{uuid.uuid4().hex}",
            now=end_of_month, estimate_usd=0.02, count_as_turn=False,
        )
        assert not full.allowed  # October: 0.02 outstanding + 0.02 > 0.03
        november = await TenantAIAllowanceService.reserve(
            session, organization_id=org_id, configuration=config,
            key=f"{STAFF_KEY_PREFIX}{org_id}:{uuid.uuid4().hex}",
            now=next_month, estimate_usd=0.02, count_as_turn=False,
        )
        assert november.allowed  # a new month starts with a new allowance
        # The October call finishing after midnight settles its own row.
        await TenantAIAllowanceService.settle(session, key=key, now=next_month)
        status = await session.scalar(
            select(AIUsageReservation.status).where(AIUsageReservation.reservation_key == key)
        )
        assert status == "settled"


@pytest.mark.asyncio
async def test_reservation_is_idempotent_per_key(db):
    org_id, _sub = await _tenant(db, budget=0.03, spent=0.0)
    key = f"{STAFF_KEY_PREFIX}{org_id}:{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as session:
        first = await TenantAIAllowanceService.reserve(
            session, organization_id=org_id, configuration=None, key=key,
            estimate_usd=0.02, count_as_turn=False,
        )
        again = await TenantAIAllowanceService.reserve(
            session, organization_id=org_id, configuration=None, key=key,
            estimate_usd=0.02, count_as_turn=False,
        )
    assert first.allowed and again.allowed and again.reason == "already_reserved"
    assert await _open_staff_reservations(org_id) == 1
