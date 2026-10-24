"""Provider-disconnected evaluation controls; no model client or network."""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.ai_usage_reservation import AIUsageReservation
from app.models.organization import Organization
from app.services.model_eval_controller import (
    AmbiguousModelOutcome,
    ConfirmedNoDispatchError,
    EvaluationBlockedError,
    EvaluationPolicy,
    EvaluationTransportResult,
    ExactInputTokenCountResult,
    InputTokenCountPolicy,
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
    counts_full_request = True

    def __init__(self, count: int | None = 12) -> None:
        self.fixed_count = count

    def count(self, value: str) -> int:
        return self.fixed_count if self.fixed_count is not None else len(value.split())


class _DisconnectedCountTransport:
    provider_disconnected = True

    def __init__(self, organization_id: int, *, result=12, error=None) -> None:
        self.organization_id = organization_id
        self.result = result
        self.error = error
        self.calls: list[dict] = []
        self.saw_durable_count_reservation = False

    async def count(self, **kwargs):
        self.calls.append(kwargs)
        async with AsyncSessionLocal() as other_session:
            reservation = await other_session.scalar(select(AIUsageReservation).where(
                AIUsageReservation.organization_id == self.organization_id,
                AIUsageReservation.reservation_key.like("model-eval-count:%"),
                AIUsageReservation.status == "reserved",
            ))
            self.saw_durable_count_reservation = reservation is not None
        if self.error:
            raise self.error
        if not isinstance(self.result, int):
            return self.result
        request_sha256 = hashlib.sha256(json.dumps(
            kwargs["request_payload"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")).hexdigest()
        return ExactInputTokenCountResult(self.result, request_sha256)


def _verified_count_policy(**changes):
    values = {
        "endpoint": "https://api.openai.com/v1/responses/input_tokens",
        "model": "gpt-6-luna",
        "region": "standard",
        "reservation_per_attempt_usd": 0.00001,
        "evidence_id": "synthetic-test-evidence",
        "evidence_expires_at": datetime.now(UTC) + timedelta(minutes=30),
        "independently_verified": True,
        "explicitly_approved": True,
    }
    values.update(changes)
    return InputTokenCountPolicy(**values)


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


def _counted_controller(generation_transport, count_transport, *, organization_id=1, count_policy=None):
    return ModelEvaluationController(
        policy=EvaluationPolicy(),
        exact_token_counter=None,
        count_policy=count_policy or _verified_count_policy(),
        count_transport=count_transport,
        transport=generation_transport,
        explicitly_approved=True,
        approved_run_id="approved-eval",
        approved_organization_id=organization_id,
        session_guard=_AllowActiveSession(),
    )


def _canonical_payload(serialized_input: str) -> dict:
    return {
        "model": "gpt-6-luna",
        "input": serialized_input,
        "service_tier": "default",
        "max_output_tokens": 500,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"independently_verified": False},
        {"explicitly_approved": False},
        {"endpoint": "https://api.openai.com/v1/responses"},
        {"model": "another-model"},
        {"region": "unknown"},
        {"evidence_id": ""},
        {"evidence_expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        {"reservation_per_attempt_usd": 0},
        {"reservation_per_attempt_usd": 0.001},
    ],
)
def test_count_policy_fails_closed_before_any_dispatch(changes):
    count_transport = _DisconnectedCountTransport(1)
    generation_transport = _Transport()
    with pytest.raises(EvaluationBlockedError, match="billing policy|headroom"):
        _counted_controller(
            generation_transport,
            count_transport,
            count_policy=_verified_count_policy(**changes),
        )
    assert count_transport.calls == []
    assert generation_transport.calls == []


@pytest.mark.asyncio
async def test_count_reserves_before_count_then_exact_count_gates_generation(db):
    organization = Organization(name=f"Eval count {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    count_transport = _DisconnectedCountTransport(organization.id, result=12)
    generation_transport = _InspectingTransport(organization.id)
    controller = _counted_controller(
        generation_transport, count_transport, organization_id=organization.id
    )

    answer = await controller.attempt(
        db, organization_id=organization.id, run_id="approved-eval",
        turn_number=1, retry_number=0, serialized_input="bounded full request",
        public_chat_session_id=101,
        canonical_request_payload=_canonical_payload("bounded full request"),
    )

    assert answer == "mocked response"
    assert count_transport.saw_durable_count_reservation is True
    assert count_transport.calls == [{
        "model": "gpt-6-luna",
        "request_payload": _canonical_payload("bounded full request"),
        "sdk_max_retries": 0,
    }]
    assert len(generation_transport.calls) == 1
    reservations = (await db.execute(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ).order_by(AIUsageReservation.reservation_key))).scalars().all()
    assert len(reservations) == 2
    assert all(item.status == "settled" for item in reservations)


@pytest.mark.asyncio
async def test_count_overrun_blocks_generation_after_count_charge_is_recorded(db):
    organization = Organization(name=f"Eval count overrun {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    count_transport = _DisconnectedCountTransport(organization.id, result=3001)
    generation_transport = _Transport()
    controller = _counted_controller(
        generation_transport, count_transport, organization_id=organization.id
    )

    with pytest.raises(EvaluationBlockedError, match="token cap"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="oversized full request",
            public_chat_session_id=101,
            canonical_request_payload=_canonical_payload("oversized full request"),
        )
    assert generation_transport.calls == []
    reservation = await db.scalar(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))
    assert reservation is not None and reservation.status == "settled"


@pytest.mark.asyncio
async def test_ambiguous_count_retains_reservation_and_blocks_retry(db):
    organization = Organization(name=f"Eval count timeout {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    count_transport = _DisconnectedCountTransport(
        organization.id, error=TimeoutError("unknown count outcome")
    )
    generation_transport = _Transport()
    controller = _counted_controller(
        generation_transport, count_transport, organization_id=organization.id
    )
    args = {
        "organization_id": organization.id,
        "run_id": "approved-eval",
        "turn_number": 1,
        "retry_number": 0,
        "serialized_input": "bounded full request",
        "public_chat_session_id": 101,
        "canonical_request_payload": _canonical_payload("bounded full request"),
    }

    with pytest.raises(AmbiguousModelOutcome, match="count outcome"):
        await controller.attempt(db, **args)
    with pytest.raises(EvaluationBlockedError, match="reconciliation"):
        await controller.attempt(db, **{**args, "retry_number": 1})
    assert len(count_transport.calls) == 1
    assert generation_transport.calls == []


@pytest.mark.asyncio
async def test_count_payload_digest_mismatch_is_ambiguous_and_blocks_generation(db):
    organization = Organization(name=f"Eval count digest {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    count_transport = _DisconnectedCountTransport(
        organization.id,
        result=ExactInputTokenCountResult(12, "not-the-request-digest"),
    )
    generation_transport = _Transport()
    controller = _counted_controller(
        generation_transport, count_transport, organization_id=organization.id
    )
    with pytest.raises(AmbiguousModelOutcome, match="count result"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="bounded full request",
            public_chat_session_id=101,
            canonical_request_payload=_canonical_payload("bounded full request"),
        )
    assert generation_transport.calls == []


@pytest.mark.asyncio
async def test_concurrent_count_attempt_is_blocked_by_durable_reservation(db):
    organization = Organization(name=f"Eval concurrent count {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    entered = asyncio.Event()
    release = asyncio.Event()
    count_transport = _DisconnectedCountTransport(organization.id)
    original_count = count_transport.count

    async def paused_count(**kwargs):
        entered.set()
        await release.wait()
        return await original_count(**kwargs)

    count_transport.count = paused_count
    controller = _counted_controller(
        _Transport(), count_transport, organization_id=organization.id
    )
    first = asyncio.create_task(controller.attempt(
        db, organization_id=organization.id, run_id="approved-eval",
        turn_number=1, retry_number=0, serialized_input="first full request",
        public_chat_session_id=101,
        canonical_request_payload=_canonical_payload("first full request"),
    ))
    await asyncio.wait_for(entered.wait(), timeout=2)
    async with AsyncSessionLocal() as other_db:
        with pytest.raises(EvaluationBlockedError, match="reconciliation"):
            await controller.attempt(
                other_db, organization_id=organization.id, run_id="approved-eval",
                turn_number=2, retry_number=0, serialized_input="second full request",
                public_chat_session_id=101,
                canonical_request_payload=_canonical_payload("second full request"),
            )
    release.set()
    assert await first == "mocked response"


@pytest.mark.asyncio
async def test_handoff_blocks_before_count_reservation_or_dispatch(db):
    organization = Organization(name=f"Eval pre-count handoff {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    count_transport = _DisconnectedCountTransport(organization.id)
    generation_transport = _Transport()
    controller = ModelEvaluationController(
        policy=EvaluationPolicy(), exact_token_counter=None,
        count_policy=_verified_count_policy(), count_transport=count_transport,
        transport=generation_transport, explicitly_approved=True,
        approved_run_id="approved-eval", approved_organization_id=organization.id,
        session_guard=_BlockHandedOffSession(),
    )
    with pytest.raises(EvaluationBlockedError, match="persisted session"):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="bounded full request",
            public_chat_session_id=101,
            canonical_request_payload=_canonical_payload("bounded full request"),
        )
    assert count_transport.calls == []
    assert generation_transport.calls == []
    assert await db.scalar(select(func.count()).select_from(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    )) == 0


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
    with pytest.raises(EvaluationBlockedError, match="full Responses request"):
        ModelEvaluationController(
            policy=EvaluationPolicy(), exact_token_counter=None,
            transport=transport, explicitly_approved=True,
            approved_run_id="approved-eval",
            approved_organization_id=1,
            session_guard=_AllowActiveSession(),
        )
    text_only_counter = _FakeExactCounter()
    text_only_counter.counts_full_request = False
    with pytest.raises(EvaluationBlockedError, match="full Responses request"):
        ModelEvaluationController(
            policy=EvaluationPolicy(), exact_token_counter=text_only_counter,
            transport=transport, explicitly_approved=True,
            approved_run_id="approved-eval",
            approved_organization_id=1,
            session_guard=_AllowActiveSession(),
        )
    for changes in [
        {"model": "unknown"}, {"service_tier": "regional"},
        {"cache_retention": "12h"}, {"input_rate_per_million": 0},
        {"output_rate_per_million": 0.49}, {"price_multiplier": 1.0},
        {"cost_ceiling_usd": 0.11},
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
    assert float(reservation.estimated_cost_usd) == pytest.approx(0.000605)


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
        EvaluationTransportResult("count mismatch", 11, 8),
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
        return EvaluationTransportResult("first", 12, 5)

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
            estimated_cost_usd=0.000605,
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
async def test_existing_handoff_gate_blocks_before_generation_reservation(db):
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
    reservation = await db.scalar(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))
    assert reservation is None


@pytest.mark.asyncio
async def test_same_turn_number_is_isolated_by_session_in_reservation_key(db):
    organization = Organization(name=f"Eval sessions {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    transport = _Transport()
    controller = _controller(transport, organization_id=organization.id)
    for session_id in (101, 202):
        await controller.attempt(
            db, organization_id=organization.id, run_id="approved-eval",
            turn_number=1, retry_number=0, serialized_input="bounded input",
            public_chat_session_id=session_id,
        )
    keys = list(await db.scalars(select(AIUsageReservation.reservation_key).where(
        AIUsageReservation.organization_id == organization.id,
    )))
    assert any("session-101:turn-1" in key for key in keys)
    assert any("session-202:turn-1" in key for key in keys)
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_ten_five_turn_sessions_share_one_fifty_attempt_budget(db):
    organization = Organization(name=f"Eval 10x5 {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    transport = _Transport()
    controller = _controller(transport, organization_id=organization.id)
    for session_id in range(1001, 1011):
        for turn_number in range(1, 6):
            await controller.attempt(
                db, organization_id=organization.id, run_id="approved-eval",
                turn_number=turn_number, retry_number=0,
                serialized_input="bounded input", public_chat_session_id=session_id,
            )
    reservations = int(await db.scalar(select(func.count()).select_from(
        AIUsageReservation
    ).where(
        AIUsageReservation.organization_id == organization.id,
        AIUsageReservation.reservation_key.like("model-eval:%"),
    )) or 0)
    assert reservations == 50
    assert len(transport.calls) == 50
