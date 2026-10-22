"""Provider-disconnected evaluation controls; no model client or network."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.ai_usage_reservation import AIUsageReservation
from app.models.organization import Organization
from app.services.model_eval_controller import (
    AmbiguousModelOutcome,
    ConfirmedNoDispatchError,
    EvaluationBlockedError,
    EvaluationPolicy,
    EvaluationTransportResult,
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
        return EvaluationTransportResult(
            text="mocked response", input_tokens=12, output_tokens=8
        )


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


class _AllowActiveSession:
    async def assert_generation_allowed(self, _db, **_kwargs):
        return None


class _BlockHandedOffSession:
    async def assert_generation_allowed(self, _db, **_kwargs):
        raise EvaluationBlockedError("persisted session state suppresses autonomous generation")


def _controller(transport, *, organization_id=1, **changes):
    run_id = changes.pop("approved_run_id", "approved-eval")
    return ModelEvaluationController(
        policy=EvaluationPolicy(**changes),
        exact_token_counter=_FakeExactCounter(),
        transport=transport,
        explicitly_approved=True,
        approved_run_id=run_id,
        approved_organization_id=organization_id,
        session_guard=_AllowActiveSession(),
    )


def test_controller_fails_closed_without_approval_tokenizer_or_known_policy():
    transport = _Transport()
    with pytest.raises(EvaluationBlockedError, match="approval"):
        ModelEvaluationController(
            policy=EvaluationPolicy(), exact_token_counter=_FakeExactCounter(),
            transport=transport, explicitly_approved=False,
            approved_run_id="approved-eval",
            approved_organization_id=1,
            session_guard=_AllowActiveSession(),
        )
    with pytest.raises(EvaluationBlockedError, match="tokenizer"):
        ModelEvaluationController(
            policy=EvaluationPolicy(), exact_token_counter=None,
            transport=transport, explicitly_approved=True,
            approved_run_id="approved-eval",
            approved_organization_id=1,
            session_guard=_AllowActiveSession(),
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
        public_chat_session_id=101,
    )

    assert result == "mocked response"
    assert transport.saw_durable_reservation is True
    assert transport.calls[0]["sdk_max_retries"] == 0
    assert transport.calls[0]["max_output_tokens"] == 500
    reservation = (await db.execute(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))).scalar_one()
    assert reservation.status == "settled"
    # Keep the conservative full reservation: this table's six-decimal money
    # precision cannot faithfully store every token-level sub-cent estimate.
    assert float(reservation.estimated_cost_usd) == pytest.approx(0.000625)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        "missing usage",
        EvaluationTransportResult("bad input", -1, 10),
        EvaluationTransportResult("zero input", 0, 10),
        EvaluationTransportResult("zero output", 10, 0),
        EvaluationTransportResult("input overrun", 3001, 10),
        EvaluationTransportResult("output overrun", 10, 501),
    ],
)
async def test_missing_malformed_or_overrun_usage_stays_reserved(db, result):
    organization = Organization(name=f"Eval usage {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    transport = _Transport()

    async def send(**kwargs):
        transport.calls.append(kwargs)
        return result

    transport.send = send
    controller = _controller(transport, organization_id=organization.id)
    with pytest.raises(AmbiguousModelOutcome, match="usage"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="bounded prompt",
            public_chat_session_id=101,
        )
    reservation = (await db.execute(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))).scalar_one()
    assert reservation.status == "reserved"


@pytest.mark.asyncio
async def test_duplicate_attempt_is_explicitly_blocked_without_dispatch(db):
    organization = Organization(name=f"Eval duplicate {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    transport = _Transport()
    controller = _controller(transport, organization_id=organization.id)
    arguments = {
        "organization_id": organization.id,
        "run_id": "approved-eval",
        "turn_number": 1,
        "retry_number": 0,
        "serialized_input": "bounded prompt",
        "public_chat_session_id": 101,
    }
    assert await controller.attempt(db, **arguments) == "mocked response"
    with pytest.raises(EvaluationBlockedError, match="already exists"):
        await controller.attempt(db, **arguments)
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_attempt_is_blocked_by_durable_unresolved_reservation(db):
    organization = Organization(name=f"Eval concurrent {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    entered = asyncio.Event()
    release = asyncio.Event()
    transport = _Transport()

    async def paused_send(**kwargs):
        transport.calls.append(kwargs)
        entered.set()
        await release.wait()
        return EvaluationTransportResult("first", 10, 5)

    transport.send = paused_send
    controller = _controller(transport, organization_id=organization.id)
    first = asyncio.create_task(controller.attempt(
        db, organization_id=organization.id, run_id="approved-eval",
        turn_number=1, retry_number=0, serialized_input="first prompt",
        public_chat_session_id=101,
    ))
    await asyncio.wait_for(entered.wait(), timeout=2)
    async with AsyncSessionLocal() as other_db:
        with pytest.raises(EvaluationBlockedError, match="reconciliation"):
            await controller.attempt(
                other_db, organization_id=organization.id, run_id="approved-eval",
                turn_number=2, retry_number=0, serialized_input="second prompt",
                public_chat_session_id=102,
            )
    release.set()
    assert await first == "first"
    assert len(transport.calls) == 1


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
            public_chat_session_id=101,
        )
    reservation = (await db.execute(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))).scalar_one()
    assert reservation.status == "reserved"
    with pytest.raises(EvaluationBlockedError, match="reconciliation"):
        await controller.attempt(
            db, organization_id=organization.id, run_id=run_id,
            turn_number=1, retry_number=1, serialized_input="retry must not dispatch",
            public_chat_session_id=101,
        )


@pytest.mark.asyncio
async def test_confirmed_no_dispatch_settles_reservation_without_retry(db):
    organization = Organization(name=f"Eval rejected {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    transport = _Transport(ConfirmedNoDispatchError("rejected before dispatch"))
    controller = _controller(transport, organization_id=organization.id)
    with pytest.raises(ConfirmedNoDispatchError, match="before dispatch"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="bounded prompt",
            public_chat_session_id=101,
        )
    reservation = (await db.execute(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))).scalar_one()
    assert reservation.status == "settled"


@pytest.mark.asyncio
async def test_transport_validation_failure_after_dispatch_is_ambiguous(db):
    organization = Organization(name=f"Eval response failure {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    controller = _controller(
        _Transport(EvaluationBlockedError("malformed provider response")),
        organization_id=organization.id,
    )

    with pytest.raises(AmbiguousModelOutcome):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="bounded prompt",
            public_chat_session_id=101,
        )
    reservation = (await db.execute(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))).scalar_one()
    assert reservation.status == "reserved"


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
        session_guard=_AllowActiveSession(),
    )
    with pytest.raises(EvaluationBlockedError, match="token cap"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="three token prompt",
            public_chat_session_id=101,
        )
    with pytest.raises(EvaluationBlockedError, match="turn cap"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=51, retry_number=0, serialized_input="two tokens",
            public_chat_session_id=101,
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
            public_chat_session_id=101,
        )
    assert transport.calls == []


@pytest.mark.asyncio
async def test_last_moment_handoff_gate_blocks_after_durable_reservation(db):
    organization = Organization(name=f"Eval handoff {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    transport = _Transport()
    controller = ModelEvaluationController(
        policy=EvaluationPolicy(), exact_token_counter=_FakeExactCounter(),
        transport=transport, explicitly_approved=True,
        approved_run_id="approved-eval",
        approved_organization_id=organization.id,
        session_guard=_BlockHandedOffSession(),
    )

    with pytest.raises(EvaluationBlockedError, match="persisted session state"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="bounded input",
            public_chat_session_id=101,
        )

    assert transport.calls == []
    reservation = (await db.execute(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))).scalar_one()
    assert reservation.status == "settled"
