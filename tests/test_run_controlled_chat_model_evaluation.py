from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.model_eval_controller import EvaluationBlockedError
from scripts import run_controlled_chat_model_evaluation as runner


def _args(*, execute_live: bool = False):
    expiry = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    return SimpleNamespace(
        organization_id=7,
        widget_key="synthetic-public-widget-key",
        embedding_origin="http://127.0.0.1:3000",
        run_id="offline-rag-candidate",
        expires_at=expiry,
        count_evidence_id="case-18041142-reviewed-screenshot",
        count_evidence_expires_at=expiry,
        all_in_evidence_id="synthetic-reviewed-budget-evidence",
        all_in_evidence_expires_at=expiry,
        additional_all_in_reserve_usd=0.00484,
        execute_live=execute_live,
    )


def _bindings():
    return [
        runner.SessionBinding(f"fixture-{index}", index, f"token-{index}")
        for index in range(1, 11)
    ]


def _approved_runtime():
    expiry = datetime.now(UTC) + timedelta(minutes=30)
    return (
        {"synthetic": True},
        runner.InputTokenCountPolicy(
            endpoint="https://api.openai.com/v1/responses/input_tokens",
            model="gpt-6-luna",
            region="standard",
            reservation_per_attempt_usd=0,
            evidence_id="synthetic-reviewed",
            evidence_expires_at=expiry,
            billing_status="no_charge",
            independently_verified=True,
            explicitly_approved=True,
        ),
        runner.EvaluationPolicy(additional_all_in_reserve_usd=0.00484),
    )


@pytest.fixture(autouse=True)
def stable_source_identity(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_source_identity",
        lambda: {
            "frozen_fixture_sha256": "a" * 64,
            "evaluation_spec_sha256": "b" * 64,
            "runner_source_sha256": "c" * 64,
            "reviewed_source_commit": "d" * 40,
        },
    )


@pytest.mark.asyncio
async def test_default_runner_is_preflight_only_and_never_prompts_or_dispatches(
    monkeypatch, capsys
):
    cleanup = []
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", False)
    monkeypatch.setattr(settings, "public_chat_model_eval_enabled", False)
    monkeypatch.setattr(runner, "_create_sessions", lambda _args: _async(_bindings()))
    monkeypatch.setattr(
        runner,
        "_close_sessions",
        lambda items: _async(cleanup.extend(item.session_id for item in items)),
    )

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("preflight must not check approval or dispatch")

    monkeypatch.setattr(runner, "_require_approval", forbidden)
    monkeypatch.setattr(runner, "_execute_turns", forbidden)
    result = await runner._run(_args())

    assert result["status"] == "preflight_complete_no_provider_access"
    assert result["expected_operations"] == {
        "turns": 50,
        "maximum_count_requests": 100,
        "maximum_generation_requests": 100,
        "sdk_retries": 0,
    }
    output = capsys.readouterr().out
    assert "activation_manifest" in output
    assert "token-1" not in output
    assert "API key" not in output
    assert cleanup == list(range(1, 11))


@pytest.mark.asyncio
async def test_live_runner_checks_exact_approval_before_masked_secret_and_rolls_back(
    monkeypatch,
):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", True)
    monkeypatch.setattr(settings, "public_chat_model_eval_enabled", True)
    monkeypatch.setattr(runner, "_create_sessions", lambda _args: _async(_bindings()))
    monkeypatch.setattr(runner, "_close_sessions", lambda _items: _async(None))
    monkeypatch.setattr(runner.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(runner.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    events: list[str] = []

    async def approved(*_args, **_kwargs):
        events.append("approval")
        return _approved_runtime()

    @contextmanager
    def secret():
        events.append("secret")
        yield "synthetic-never-networked-key"
        events.append("secret-dropped")

    class Client:
        async def aclose(self):
            events.append("client-closed")

    @asynccontextmanager
    async def lifecycle(**_kwargs):
        events.append("provider-installed")
        yield object()
        events.append("provider-removed")

    async def execute(_bindings, **_kwargs):
        events.append("50-turn-orchestrator")
        return {"turns_attempted": 50}

    monkeypatch.setattr(runner, "_require_approval", approved)
    monkeypatch.setattr(runner, "operator_entered_api_key", secret)
    monkeypatch.setattr(runner, "NoRetryHttpClient", Client)
    monkeypatch.setattr(runner, "installed_development_evaluation_provider", lifecycle)
    monkeypatch.setattr(runner, "_execute_turns", execute)

    result = await runner._run(_args(execute_live=True))
    assert result == {"turns_attempted": 50}
    assert events == [
        "approval",
        "secret",
        "provider-installed",
        "50-turn-orchestrator",
        "provider-removed",
        "secret-dropped",
        "client-closed",
    ]


@pytest.mark.asyncio
async def test_denied_approval_never_constructs_client_or_prompts(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", True)
    monkeypatch.setattr(settings, "public_chat_model_eval_enabled", True)
    monkeypatch.setattr(runner, "_create_sessions", lambda _args: _async(_bindings()))
    monkeypatch.setattr(runner, "_close_sessions", lambda _items: _async(None))
    monkeypatch.setattr(runner.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(runner.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    async def denied(*_args, **_kwargs):
        raise EvaluationBlockedError("durable evaluation approval is missing")

    monkeypatch.setattr(runner, "_require_approval", denied)
    monkeypatch.setattr(
        runner,
        "NoRetryHttpClient",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be built")),
    )
    with pytest.raises(EvaluationBlockedError, match="approval is missing"):
        await runner._run(_args(execute_live=True))


@pytest.mark.asyncio
async def test_live_interruption_removes_provider_key_client_and_sessions(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", True)
    monkeypatch.setattr(settings, "public_chat_model_eval_enabled", True)
    monkeypatch.setattr(runner, "_create_sessions", lambda _args: _async(_bindings()))
    monkeypatch.setattr(runner.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(runner.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    events = []

    async def approved(*_args, **_kwargs):
        events.append("approval")
        return _approved_runtime()

    @contextmanager
    def secret():
        events.append("secret")
        try:
            yield "synthetic-never-networked-key"
        finally:
            events.append("secret-dropped")

    class Client:
        async def aclose(self):
            events.append("client-closed")

    @asynccontextmanager
    async def lifecycle(**_kwargs):
        events.append("provider-installed")
        try:
            yield object()
        finally:
            events.append("provider-removed")

    async def interrupted(*_args, **_kwargs):
        events.append("interrupted")
        raise KeyboardInterrupt

    async def cleanup(items):
        events.append(f"sessions-closed:{len(items)}")
        return []

    monkeypatch.setattr(runner, "_require_approval", approved)
    monkeypatch.setattr(runner, "operator_entered_api_key", secret)
    monkeypatch.setattr(runner, "NoRetryHttpClient", Client)
    monkeypatch.setattr(runner, "installed_development_evaluation_provider", lifecycle)
    monkeypatch.setattr(runner, "_execute_turns", interrupted)
    monkeypatch.setattr(runner, "_close_sessions", cleanup)

    with pytest.raises(KeyboardInterrupt):
        await runner._run(_args(execute_live=True))
    assert events == [
        "approval",
        "secret",
        "provider-installed",
        "interrupted",
        "provider-removed",
        "secret-dropped",
        "client-closed",
        "sessions-closed:10",
    ]


@pytest.mark.asyncio
async def test_turn_orchestrator_preserves_frozen_order_and_all_ten_sessions(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def send(_db, *, session_token, text, client_message_id, **_kwargs):
        calls.append((session_token, client_message_id))
        return {
            "status": "ai_active",
            "reply": f"synthetic:{text}",
            "handoff": False,
            "_model_eval_request_id": f"request-{client_message_id}",
        }

    monkeypatch.setattr(runner.PublicChatService, "send_message", send)
    monkeypatch.setattr(
        runner, "_persist_protocol_results", lambda **_kwargs: _async(None)
    )

    class Scalar:
        calls = 0

        async def scalar(self, _query):
            self.calls += 1
            if self.calls % 3 in {1, 2}:
                return 0
            return SimpleNamespace(
                sources=[{"source_id": "synthetic"}],
                llm_called=False,
                model="mocked-gpt-6-luna-contract",
                request_id="mock-request",
            )

    class DBContext:
        async def __aenter__(self):
            return Scalar()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(runner, "AsyncSessionLocal", DBContext)
    # Build from the canonical first-seen order rather than duplicating fixture ids.
    seen = []
    for turn in runner.expanded_turns():
        if turn["conversation_id"] not in seen:
            seen.append(turn["conversation_id"])
    bindings = [
        runner.SessionBinding(conversation_id, index, f"token-{index}")
        for index, conversation_id in enumerate(seen, 1)
    ]

    result = await runner._execute_turns(
        bindings,
        organization_id=7,
        approved_run_id="offline-rag-candidate",
        activation_manifest={"synthetic": True},
        embedding_origin="http://127.0.0.1:3000",
    )
    assert result["turns_attempted"] == 50
    assert result["sessions"] == 10
    assert [item[1] for item in calls] == [
        f"eval-{turn['turn_id']}" for turn in runner.expanded_turns()
    ]
    assert len({item[0] for item in calls}) == 10
    assert all(item["provider_path_observed"] is False for item in result["outcomes"])
    assert all(item["mock_mechanics_observed"] is True for item in result["outcomes"])


@pytest.mark.asyncio
@pytest.mark.parametrize("reservation_counts", ([1, 0], [0, 1]))
async def test_ambiguous_reservation_stops_after_first_turn_and_persists_failure(
    monkeypatch,
    reservation_counts,
):
    calls = 0
    persisted = []

    class Scalar:
        calls = 0

        async def scalar(self, _query):
            self.calls += 1
            if self.calls <= 2:
                return reservation_counts[self.calls - 1]
            return None

    class DBContext:
        async def __aenter__(self):
            return Scalar()

        async def __aexit__(self, *_args):
            return False

    async def send(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "status": "human_requested",
            "reply": "synthetic handoff",
            "handoff": True,
        }

    async def persist(**kwargs):
        persisted.append(kwargs)

    monkeypatch.setattr(runner, "AsyncSessionLocal", DBContext)
    monkeypatch.setattr(runner.PublicChatService, "send_message", send)
    monkeypatch.setattr(runner, "_persist_protocol_results", persist)
    seen = list(dict.fromkeys(turn["conversation_id"] for turn in runner.expanded_turns()))
    bindings = [
        runner.SessionBinding(conversation_id, index, f"token-{index}")
        for index, conversation_id in enumerate(seen, 1)
    ]

    with pytest.raises(runner.RunnerBlocked, match="reconciliation"):
        await runner._execute_turns(
            bindings,
            organization_id=7,
            approved_run_id="offline-rag-candidate",
            activation_manifest={"synthetic": True},
            embedding_origin="http://127.0.0.1:3000",
        )
    assert calls == 1
    assert persisted[0]["succeeded"] is False
    assert len(persisted[0]["outcomes"]) == 1


@pytest.mark.asyncio
async def test_turn_interruption_persists_partial_failure(monkeypatch):
    sends = 0
    persisted = []

    class Scalar:
        calls = 0

        async def scalar(self, _query):
            self.calls += 1
            if self.calls % 3 in {1, 2}:
                return 0
            return SimpleNamespace(
                sources=[{"source_id": "synthetic"}],
                llm_called=False,
                model="mocked-gpt-6-luna-contract",
                request_id="mock-request",
            )

    class DBContext:
        async def __aenter__(self):
            return Scalar()

        async def __aexit__(self, *_args):
            return False

    async def send(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        if sends == 2:
            raise KeyboardInterrupt
        return {"status": "ai_active", "reply": "synthetic", "handoff": False}

    async def persist(**kwargs):
        persisted.append(kwargs)

    monkeypatch.setattr(runner, "AsyncSessionLocal", DBContext)
    monkeypatch.setattr(runner.PublicChatService, "send_message", send)
    monkeypatch.setattr(runner, "_persist_protocol_results", persist)
    seen = list(dict.fromkeys(turn["conversation_id"] for turn in runner.expanded_turns()))
    bindings = [
        runner.SessionBinding(conversation_id, index, f"token-{index}")
        for index, conversation_id in enumerate(seen, 1)
    ]

    with pytest.raises(KeyboardInterrupt):
        await runner._execute_turns(
            bindings,
            organization_id=7,
            approved_run_id="offline-rag-candidate",
            activation_manifest={"synthetic": True},
            embedding_origin="http://127.0.0.1:3000",
        )
    assert persisted[0]["succeeded"] is False
    assert persisted[0]["error_category"] == "interrupted"
    assert len(persisted[0]["outcomes"]) == 1


async def _async(value):
    return value
