"""Provider-disconnected evaluation controls; no model client or network."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.ai_usage_reservation import AIUsageReservation
from app.models.organization import Organization
from app.services.model_eval_controller import (
    AmbiguousModelOutcome,
    EvaluationBlockedError,
    EvaluationPolicy,
    ModelEvaluationController,
)


class _Transport:
    provider_disconnected = True

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def send(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return "mocked response"


class _InspectingTransport(_Transport):
    def __init__(self, organization_id: int) -> None:
        super().__init__()
        self.organization_id = organization_id
        self.saw_durable_reservation = False

    async def send(self, **kwargs):
        async with AsyncSessionLocal() as other_session:
            reservation = await other_session.scalar(select(AIUsageReservation).where(
                AIUsageReservation.organization_id == self.organization_id,
                AIUsageReservation.status == "reserved",
            ))
            self.saw_durable_reservation = reservation is not None
        return await super().send(**kwargs)


class _FakeExactCounter:
    model = "gpt-6-luna"

    def __init__(self, count: int | None = None) -> None:
        self.fixed_count = count

    def count(self, value: str) -> int:
        return self.fixed_count if self.fixed_count is not None else len(value.split())


def _controller(transport, *, organization_id=1, **changes):
    run_id = changes.pop("approved_run_id", "approved-eval")
    return ModelEvaluationController(
        policy=EvaluationPolicy(**changes),
        exact_token_counter=_FakeExactCounter(),
        transport=transport,
        explicitly_approved=True,
        approved_run_id=run_id,
        approved_organization_id=organization_id,
    )


def test_controller_fails_closed_without_approval_tokenizer_or_known_policy():
    transport = _Transport()
    with pytest.raises(EvaluationBlockedError, match="approval"):
        ModelEvaluationController(
            policy=EvaluationPolicy(), exact_token_counter=_FakeExactCounter(),
            transport=transport, explicitly_approved=False,
            approved_run_id="approved-eval",
            approved_organization_id=1,
        )
    with pytest.raises(EvaluationBlockedError, match="tokenizer"):
        ModelEvaluationController(
            policy=EvaluationPolicy(), exact_token_counter=None,
            transport=transport, explicitly_approved=True,
            approved_run_id="approved-eval",
            approved_organization_id=1,
        )
    for changes in [
        {"model": "unknown"}, {"service_tier": "regional"},
        {"cache_retention": "12h"}, {"input_rate_per_million": 0},
        {"output_rate_per_million": 0.49}, {"cost_ceiling_usd": 0.11},
    ]:
        with pytest.raises(EvaluationBlockedError):
            _controller(transport, **changes)


@pytest.mark.asyncio
async def test_reservation_is_durable_before_dispatch_and_sdk_retry_is_zero(db):
    organization = Organization(name=f"Eval mock {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    transport = _InspectingTransport(organization.id)
    controller = _controller(transport, organization_id=organization.id)

    result = await controller.attempt(
        db, organization_id=organization.id, run_id="approved-eval",
        turn_number=1, retry_number=0, serialized_input="full serialized prompt",
    )

    assert result == "mocked response"
    assert transport.saw_durable_reservation is True
    assert transport.calls[0]["sdk_max_retries"] == 0
    assert transport.calls[0]["max_output_tokens"] == 500
    reservation = (await db.execute(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))).scalar_one()
    assert reservation.status == "settled"


@pytest.mark.asyncio
async def test_ambiguous_timeout_keeps_reservation_and_stops_run(db):
    organization = Organization(name=f"Eval timeout {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    run_id = "approved-eval"
    controller = _controller(
        _Transport(ConnectionError("unknown outcome")),
        organization_id=organization.id,
    )

    with pytest.raises(AmbiguousModelOutcome):
        await controller.attempt(
            db, organization_id=organization.id, run_id=run_id,
            turn_number=1, retry_number=0, serialized_input="bounded prompt",
        )
    reservation = (await db.execute(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))).scalar_one()
    assert reservation.status == "reserved"
    with pytest.raises(EvaluationBlockedError, match="reconciliation"):
        await controller.attempt(
            db, organization_id=organization.id, run_id=run_id,
            turn_number=1, retry_number=1, serialized_input="retry must not dispatch",
        )


@pytest.mark.asyncio
async def test_full_input_and_attempt_caps_block_before_transport(db):
    organization = Organization(name=f"Eval caps {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    transport = _Transport()
    controller = ModelEvaluationController(
        policy=EvaluationPolicy(), exact_token_counter=_FakeExactCounter(3001),
        transport=transport, explicitly_approved=True,
        approved_run_id="approved-eval",
        approved_organization_id=organization.id,
    )
    with pytest.raises(EvaluationBlockedError, match="token cap"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="three token prompt",
        )
    with pytest.raises(EvaluationBlockedError, match="turn cap"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=51, retry_number=0, serialized_input="two tokens",
        )
    assert transport.calls == []


@pytest.mark.asyncio
async def test_persisted_hundred_attempt_cap_blocks_another_dispatch(db):
    organization = Organization(name=f"Eval hundred {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    now = datetime.now(UTC)
    db.add_all([
        AIUsageReservation(
            organization_id=organization.id,
            reservation_key=f"model-eval:{organization.id}:approved-eval:seed-{index}",
            period_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            status="settled",
            estimated_cost_usd=0.000625,
            expires_at=now + timedelta(days=30),
            settled_at=now,
        )
        for index in range(100)
    ])
    await db.commit()
    transport = _Transport()
    controller = _controller(transport, organization_id=organization.id)
    with pytest.raises(EvaluationBlockedError, match="attempt cap"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=50, retry_number=1, serialized_input="bounded input",
        )
    assert transport.calls == []
