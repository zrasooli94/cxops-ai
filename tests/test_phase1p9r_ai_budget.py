"""Phase 1P.9R — strict, concurrency-safe AI allowances.

Phase 1P.9 *read* month-to-date usage before each turn, so N turns admitted at
the same moment could all pass a nearly-exhausted check. Admission is now a
reservation serialised by one transaction-scoped advisory lock, so it holds
across API instances (simulated here with independent engines/connection
pools against the same database).

Covers: one tenant, several tenants, 50 simultaneous tenants, the platform
ceiling, duplicate keys, month boundaries, crashed (expired) reservations,
settlement, and the end-to-end exhausted-tenant fallback. Disposable DB only.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings as current_settings
from app.models.ai_request_log import AIRequestLog
from app.models.ai_usage_reservation import AIUsageReservation
from app.services import tenant_ai_allowance_service as allowance_module
from app.services.tenant_ai_allowance_service import TenantAIAllowanceService
from tests import test_public_chat as base

_configure_auth = base._configure_auth
db = base.db
org_scope = base.org_scope
_phase1p_namespace_isolated = base._phase1p_namespace_isolated

ESTIMATE = 0.01


@pytest.fixture(autouse=True)
def _budget_settings(monkeypatch):
    svc = allowance_module.settings
    monkeypatch.setattr(svc, "public_chat_turn_reserved_cost_usd", ESTIMATE)
    monkeypatch.setattr(svc, "public_chat_reservation_ttl_seconds", 900)
    monkeypatch.setattr(svc, "platform_monthly_ai_budget_usd", 1_000_000.0)
    monkeypatch.setattr(svc, "public_chat_default_monthly_ai_turns", 3000)
    monkeypatch.setattr(svc, "public_chat_default_monthly_ai_budget_usd", 5.0)


@pytest_asyncio.fixture
async def instances():
    """Two independent engines = two API instances sharing one database."""
    # Bounded pools, like real API instances (each request waits for one of
    # its instance's connections; the advisory lock serialises across both).
    engines = [
        create_async_engine(current_settings.database_url, pool_size=10, max_overflow=0)
        for _ in range(2)
    ]
    makers = [async_sessionmaker(e, class_=AsyncSession, expire_on_commit=False) for e in engines]
    yield makers
    for engine in engines:
        await engine.dispose()


async def _tenant(db, org_scope, *, turns: int | None = None, budget: float | None = None):
    org = await org_scope()
    config, _key = await base._make_widget_config(db, org)
    config.monthly_ai_turn_limit = turns
    config.monthly_ai_budget_usd = budget
    await db.commit()
    return org.id, config


async def _reserve(maker, org_id, config, key, now=None):
    async with maker() as session:
        return await TenantAIAllowanceService.reserve(
            session, organization_id=org_id, configuration=config, key=key, now=now
        )


async def _log_cost(db, org_id: int, usd: float, at: datetime | None = None) -> None:
    db.add(
        AIRequestLog(
            organization_id=org_id,
            request_id=uuid.uuid4().hex,
            feature="agent_decision",
            model="gpt-4o-mini",
            status="success",
            question="[usage]",
            grounded=False,
            llm_called=True,
            retrieval_count=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_usd=usd,
            latency_ms=0.0,
            sources=[],
            **({"created_at": at} if at else {}),
        )
    )
    await db.commit()


def _keys(n: int) -> list[str]:
    return [f"ai-turn:test:{uuid.uuid4().hex}" for _ in range(n)]


# ---------------------------------------------------------------------------
# Concurrency at the boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_turns_never_exceed_the_turn_limit_across_instances(
    db, org_scope, instances
):
    org_id, config = await _tenant(db, org_scope, turns=3)
    keys = _keys(24)
    decisions = await asyncio.gather(
        *(_reserve(instances[i % 2], org_id, config, k) for i, k in enumerate(keys))
    )
    assert sum(d.allowed for d in decisions) == 3
    assert Counter(d.reason for d in decisions if not d.allowed) == {"turn_limit_reached": 21}


@pytest.mark.asyncio
async def test_concurrent_turns_never_exceed_the_usd_budget_across_instances(
    db, org_scope, instances
):
    org_id, config = await _tenant(db, org_scope, budget=0.10)
    await _log_cost(db, org_id, 0.05)  # already spent this month
    decisions = await asyncio.gather(
        *(_reserve(instances[i % 2], org_id, config, k) for i, k in enumerate(_keys(20)))
    )
    # $0.05 left, $0.01 reserved per turn: exactly 5 admitted, never 6+.
    assert sum(d.allowed for d in decisions) == 5
    assert {d.reason for d in decisions if not d.allowed} == {"budget_reached"}


@pytest.mark.asyncio
async def test_several_tenants_are_limited_independently(db, org_scope, instances):
    tenants = [await _tenant(db, org_scope, turns=2) for _ in range(5)]
    calls = [
        _reserve(instances[i % 2], org_id, config, key)
        for org_id, config in tenants
        for i, key in enumerate(_keys(6))
    ]
    decisions = await asyncio.gather(*calls)
    per_tenant = Counter()
    for (org_id, _), chunk in zip(tenants, [decisions[i : i + 6] for i in range(0, 30, 6)], strict=True):
        per_tenant[org_id] = sum(d.allowed for d in chunk)
    assert set(per_tenant.values()) == {2}


@pytest.mark.asyncio
async def test_fifty_simultaneous_tenants_each_get_exactly_their_allowance(
    db, org_scope, instances
):
    tenants = [await _tenant(db, org_scope, turns=1) for _ in range(50)]
    plan = [(org_id, config, key) for org_id, config in tenants for key in _keys(3)]
    decisions = await asyncio.gather(
        *(_reserve(instances[i % 2], o, c, k) for i, (o, c, k) in enumerate(plan))
    )
    admitted = Counter(o for (o, _c, _k), d in zip(plan, decisions, strict=True) if d.allowed)
    assert len(admitted) == 50 and set(admitted.values()) == {1}


@pytest.mark.asyncio
async def test_platform_ceiling_holds_across_tenants_and_instances(
    db, org_scope, instances, monkeypatch
):
    tenants = [await _tenant(db, org_scope) for _ in range(10)]
    # Measure what the rest of the (shared) test database already spent this
    # month, then leave room for exactly four $0.01 turns on top of it.
    async with instances[0]() as session:
        baseline = await TenantAIAllowanceService._usage(
            session,
            organization_id=None,
            period_start=allowance_module.month_start(),
            now=datetime.now(UTC),
        )
    monkeypatch.setattr(
        allowance_module.settings,
        "platform_monthly_ai_budget_usd",
        baseline.spent_usd + baseline.outstanding_usd + 4 * ESTIMATE + 0.001,
    )
    decisions = await asyncio.gather(
        *(
            _reserve(instances[i % 2], org_id, config, key)
            for i, ((org_id, config), key) in enumerate(zip(tenants * 2, _keys(20), strict=True))
        )
    )
    assert sum(d.allowed for d in decisions) == 4
    assert {d.reason for d in decisions if not d.allowed} == {"platform_budget_reached"}


# ---------------------------------------------------------------------------
# Idempotency, settlement, expiry, month boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_requests_reserve_and_count_once(db, org_scope, instances):
    org_id, config = await _tenant(db, org_scope, turns=1)
    key = _keys(1)[0]
    decisions = await asyncio.gather(*(_reserve(instances[i % 2], org_id, config, key) for i in range(6)))
    assert all(d.allowed for d in decisions)
    rows = (
        await db.execute(
            select(AIUsageReservation).where(AIUsageReservation.organization_id == org_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    # The single turn consumed the single allowed turn; a new turn is refused.
    assert not (await _reserve(instances[0], org_id, config, _keys(1)[0])).allowed


@pytest.mark.asyncio
async def test_settled_turn_counts_its_logged_cost_not_the_estimate(db, org_scope, instances):
    org_id, config = await _tenant(db, org_scope, budget=0.02)
    key = _keys(1)[0]
    assert (await _reserve(instances[0], org_id, config, key)).allowed
    async with instances[1]() as session:
        await _log_cost(session, org_id, 0.001)  # what the turn really cost
        await TenantAIAllowanceService.settle(session, key=key)
        await TenantAIAllowanceService.settle(session, key=key)  # idempotent
        view = await TenantAIAllowanceService.check(
            session, organization_id=org_id, configuration=config
        )
    assert view.outstanding_usd == 0
    assert view.cost_used_usd == pytest.approx(0.001 + allowance_module.UNMETERED_COST_PER_TURN_USD)
    assert (await _reserve(instances[0], org_id, config, _keys(1)[0])).allowed


@pytest.mark.asyncio
async def test_crashed_reservation_stops_holding_budget_after_its_ttl(db, org_scope, instances):
    org_id, config = await _tenant(db, org_scope, budget=0.015)
    started = datetime.now(UTC)
    assert (await _reserve(instances[0], org_id, config, _keys(1)[0], now=started)).allowed
    # Within the TTL the crashed turn's estimate is still held.
    soon = started + timedelta(seconds=60)
    assert not (await _reserve(instances[1], org_id, config, _keys(1)[0], now=soon)).allowed
    # After the TTL it no longer blocks the tenant (its model calls, if any,
    # are in ai_request_logs) - but it still counts as a turn.
    later = started + timedelta(seconds=901)
    decision = await _reserve(instances[1], org_id, config, _keys(1)[0], now=later)
    assert decision.allowed and decision.turns_used == 2


@pytest.mark.asyncio
async def test_month_boundary_starts_a_fresh_allowance(db, org_scope, instances):
    org_id, config = await _tenant(db, org_scope, turns=1)
    last_second = datetime(2031, 1, 31, 23, 59, 59, tzinfo=UTC)
    first_second = datetime(2031, 2, 1, 0, 0, 0, tzinfo=UTC)
    assert (await _reserve(instances[0], org_id, config, _keys(1)[0], now=last_second)).allowed
    assert not (await _reserve(instances[0], org_id, config, _keys(1)[0], now=last_second)).allowed
    february = await _reserve(instances[1], org_id, config, _keys(1)[0], now=first_second)
    assert february.allowed
    assert february.period_start == first_second


@pytest.mark.asyncio
async def test_zero_budget_refuses_without_reserving(db, org_scope, instances):
    org_id, config = await _tenant(db, org_scope, budget=0.0)
    decision = await _reserve(instances[0], org_id, config, _keys(1)[0])
    assert not decision.allowed and decision.reason == "budget_reached"
    rows = (
        await db.execute(
            select(AIUsageReservation).where(AIUsageReservation.organization_id == org_id)
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_allowance_report_labels_figures_as_estimates(db, org_scope):
    org_id, config = await _tenant(db, org_scope)
    view = await TenantAIAllowanceService.check(db, organization_id=org_id, configuration=config)
    payload = view.as_dict()
    assert payload["cost_basis"] == "estimated"
    assert "estimated_cost_used_usd" in payload and "estimated_outstanding_usd" in payload
