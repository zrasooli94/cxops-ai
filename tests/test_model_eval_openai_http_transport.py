"""Mock-only HTTP boundary tests; no sockets, credentials, or provider calls."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.models.ai_usage_reservation import AIUsageReservation
from app.models.organization import Organization
from app.services.model_eval_controller import (
    AmbiguousModelOutcome,
    ConfirmedNoDispatchError,
    EvaluationBlockedError,
    EvaluationPolicy,
    ModelEvaluationController,
)
from app.services.model_eval_openai_adapter import (
    DisconnectedResponsesEvaluationTransport,
    GroundedTurn,
    ProviderDisconnectedResponsesAdapter,
)
from app.services.model_eval_openai_http_transport import (
    ProviderDisconnectedResponsesHttpTransport,
)


class _Response:
    def __init__(self, body=None, *, status=200, content_type="application/json"):
        self.body = body if body is not None else _completed_response()
        self.status_code = status
        self.headers = {"content-type": content_type}

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class _Client:
    provider_disconnected = True
    max_retries = 0

    def __init__(self, response=None, error=None):
        self.response = response or _Response()
        self.error = error
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def _completed_response():
    return {
        "status": "completed",
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "output": [{"content": [{
            "type": "output_text",
            "text": json.dumps({
                "answer": "Open until 19:15.",
                "citation_ids": ["hours"],
                "refused": False,
            }),
        }]}],
    }


def _payload(input_text="bounded input"):
    return ProviderDisconnectedResponsesAdapter.request_payload(input_text)


class _ExactCounter:
    model = "gpt-6-luna"

    def count(self, _value):
        return 100


class _AllowSession:
    async def assert_generation_allowed(self, _db, **_kwargs):
        return None


class _BlockSession:
    async def assert_generation_allowed(self, _db, **_kwargs):
        raise EvaluationBlockedError("persisted session suppresses generation")


def _serialized_input():
    return ProviderDisconnectedResponsesAdapter.serialize_grounded_input(
        session_status="ai_active",
        assistant_name="Shop assistant",
        language="en",
        history=[GroundedTurn("customer", "When do you close?")],
        knowledge=[("hours", "The workshop closes at 19:15.")],
        customer_message="Can I arrive at 19:00?",
    )


def test_requires_disconnected_client_and_has_no_endpoint_or_auth_configuration():
    client = _Client()
    client.provider_disconnected = False
    with pytest.raises(EvaluationBlockedError, match="disconnected"):
        ProviderDisconnectedResponsesHttpTransport(client)

    transport = ProviderDisconnectedResponsesHttpTransport(_Client())
    assert transport.ENDPOINT == "mock://provider-disconnected/v1/responses"
    assert not transport.ENDPOINT.startswith(("http://", "https://"))
    assert not hasattr(transport, "api_key")


@pytest.mark.asyncio
async def test_posts_once_to_fixed_endpoint_without_authorization_or_retry():
    client = _Client()
    result = await ProviderDisconnectedResponsesHttpTransport(client).create_response(
        _payload()
    )
    assert result["usage"]["input_tokens"] == 100
    assert len(client.calls) == 1
    url, options = client.calls[0]
    assert url == "mock://provider-disconnected/v1/responses"
    assert options["timeout"] == 30.0
    assert options["headers"] == {"Content-Type": "application/json"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"model": "other"},
        {"service_tier": "priority"},
        {"max_output_tokens": 501},
        {"stream": True},
        {"store": True},
        {"prompt_cache_options": {"ttl": "12h"}},
    ],
)
async def test_policy_or_schema_mismatch_is_confirmed_before_dispatch(change):
    client = _Client()
    payload = _payload()
    payload.update(change)
    with pytest.raises(ConfirmedNoDispatchError, match="policy"):
        await ProviderDisconnectedResponsesHttpTransport(client).create_response(payload)
    assert client.calls == []


@pytest.mark.asyncio
async def test_request_byte_limit_is_confirmed_before_dispatch():
    client = _Client()
    with pytest.raises(ConfirmedNoDispatchError, match="byte"):
        await ProviderDisconnectedResponsesHttpTransport(client).create_response(
            _payload("x" * 70_000)
        )
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, message",
    [
        (_Response(status=429), "HTTP status 429"),
        (_Response(status=500), "HTTP status 500"),
        (_Response(content_type="text/event-stream"), "streamed"),
        (_Response(content_type="text/plain"), "not JSON"),
        (_Response(ValueError("secret body")), "malformed JSON"),
        (_Response([]), "not an object"),
    ],
)
async def test_post_dispatch_failures_are_safe_and_redacted(response, message):
    client = _Client(response=response)
    with pytest.raises(EvaluationBlockedError, match=message) as captured:
        await ProviderDisconnectedResponsesHttpTransport(client).create_response(_payload())
    assert "secret body" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_timeout_propagates_as_ambiguous_without_retry():
    client = _Client(error=TimeoutError("contains sensitive diagnostic"))
    with pytest.raises(EvaluationBlockedError, match="ambiguous") as captured:
        await ProviderDisconnectedResponsesHttpTransport(client).create_response(_payload())
    assert captured.value.__cause__ is None
    assert "sensitive diagnostic" not in str(captured.value)
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_http_failure_keeps_controller_reservation_for_reconciliation(db):
    organization = Organization(name=f"Synthetic HTTP reconciliation {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    client = _Client(response=_Response(status=429))
    http_transport = ProviderDisconnectedResponsesHttpTransport(client)
    adapter = ProviderDisconnectedResponsesAdapter(
        transport=http_transport, explicitly_approved=True
    )
    controller = ModelEvaluationController(
        policy=EvaluationPolicy(),
        exact_token_counter=_ExactCounter(),
        transport=DisconnectedResponsesEvaluationTransport(adapter),
        explicitly_approved=True,
        approved_run_id="http-contract",
        approved_organization_id=organization.id,
        session_guard=_AllowSession(),
    )

    with pytest.raises(AmbiguousModelOutcome, match="reconciliation"):
        await controller.attempt(
            db,
            organization_id=organization.id,
            run_id="http-contract",
            turn_number=1,
            retry_number=0,
            serialized_input=_serialized_input(),
            public_chat_session_id=101,
        )

    reservation = await db.scalar(
        select(AIUsageReservation).where(
            AIUsageReservation.organization_id == organization.id
        )
    )
    assert reservation is not None and reservation.status == "reserved"
    assert len(client.calls) == 1


def _controller(organization_id, client, session_guard=None):
    adapter = ProviderDisconnectedResponsesAdapter(
        transport=ProviderDisconnectedResponsesHttpTransport(client),
        explicitly_approved=True,
    )
    return ModelEvaluationController(
        policy=EvaluationPolicy(),
        exact_token_counter=_ExactCounter(),
        transport=DisconnectedResponsesEvaluationTransport(adapter),
        explicitly_approved=True,
        approved_run_id="http-contract",
        approved_organization_id=organization_id,
        session_guard=session_guard or _AllowSession(),
    )


@pytest.mark.asyncio
async def test_composed_success_settles_reservation_and_records_usage(db):
    organization = Organization(name=f"Synthetic HTTP success {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    client = _Client()

    answer = await _controller(organization.id, client).attempt(
        db,
        organization_id=organization.id,
        run_id="http-contract",
        turn_number=1,
        retry_number=0,
        serialized_input=_serialized_input(),
        public_chat_session_id=101,
    )

    reservation = await db.scalar(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))
    assert json.loads(answer)["citation_ids"] == ["hours"]
    assert reservation is not None and reservation.status == "settled"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_predispatch_schema_rejection_settles_without_http_call(db):
    organization = Organization(name=f"Synthetic HTTP predispatch {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    client = _Client()
    adapter = ProviderDisconnectedResponsesAdapter(
        transport=ProviderDisconnectedResponsesHttpTransport(client),
        explicitly_approved=True,
    )
    original = adapter.request_payload
    adapter.request_payload = lambda value: {**original(value), "stream": True}
    controller = ModelEvaluationController(
        policy=EvaluationPolicy(), exact_token_counter=_ExactCounter(),
        transport=DisconnectedResponsesEvaluationTransport(adapter),
        explicitly_approved=True, approved_run_id="http-contract",
        approved_organization_id=organization.id, session_guard=_AllowSession(),
    )

    with pytest.raises(ConfirmedNoDispatchError):
        await controller.attempt(
            db, organization_id=organization.id, run_id="http-contract",
            turn_number=1, retry_number=0, serialized_input=_serialized_input(),
            public_chat_session_id=101,
        )
    reservation = await db.scalar(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))
    assert reservation is not None and reservation.status == "settled"
    assert client.calls == []


@pytest.mark.asyncio
async def test_persisted_handoff_suppresses_before_http_call(db):
    organization = Organization(name=f"Synthetic HTTP handoff {uuid.uuid4().hex}")
    db.add(organization)
    await db.commit()
    client = _Client()
    with pytest.raises(EvaluationBlockedError, match="persisted session"):
        await _controller(organization.id, client, _BlockSession()).attempt(
            db, organization_id=organization.id, run_id="http-contract",
            turn_number=1, retry_number=0, serialized_input=_serialized_input(),
            public_chat_session_id=101,
        )
    reservation = await db.scalar(select(AIUsageReservation).where(
        AIUsageReservation.organization_id == organization.id
    ))
    assert reservation is not None and reservation.status == "settled"
    assert client.calls == []
