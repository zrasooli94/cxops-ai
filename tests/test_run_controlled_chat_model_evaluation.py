from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.model_eval_controller import EvaluationBlockedError
from scripts import run_controlled_chat_model_evaluation as runner


def _args(
    *,
    execute_live: bool = False,
    prepare_review_hold: bool = False,
    resume_review_hold=None,
):
    expiry = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    return SimpleNamespace(
        organization_id=7,
        widget_key="synthetic-public-widget-key",
        embedding_origin="http://127.0.0.1:3000",
        run_id="offline-rag-candidate",
        expires_at=expiry,
        count_evidence_id=runner.COUNT_EVIDENCE_ID,
        count_evidence_expires_at=expiry,
        all_in_evidence_id=runner.ALL_IN_EVIDENCE_ID,
        all_in_evidence_expires_at=expiry,
        additional_all_in_reserve_usd=0.00484,
        execute_live=execute_live,
        prepare_review_hold=prepare_review_hold,
        resume_review_hold=resume_review_hold,
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
            reservation_per_attempt_usd=0.00030,
            evidence_id=runner.COUNT_EVIDENCE_ID,
            evidence_expires_at=expiry,
            billing_status="unknown",
            independently_verified=False,
            explicitly_approved=True,
            conditional_policy_version=runner.CONDITIONAL_BILLING_POLICY_VERSION,
            conditional_evidence_sha256=(
                runner.CONDITIONAL_BILLING_POLICY.support_evidence_sha256
            ),
        ),
        runner.EvaluationPolicy(additional_all_in_reserve_usd=0.00484),
    )


def _promote_proposal(manifest):
    return runner.require_conditional_billing_acceptance(
        manifest,
        acceptance=runner.conditional_acceptance_request(),
        accepted_by_subject="authenticated-owner",
    )


async def _live_authorized(*_args, **_kwargs):
    return SimpleNamespace(decision="authorized")


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
async def test_review_hold_prints_exact_manifest_without_tokens_and_cleans_on_expiry(
    monkeypatch, capsys, tmp_path
):
    cleanup = []
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", False)
    monkeypatch.setattr(settings, "public_chat_model_eval_enabled", False)
    monkeypatch.setattr(runner, "REVIEW_HOLD_DIRECTORY", tmp_path)
    monkeypatch.setattr(runner, "_create_sessions", lambda _args: _async(_bindings()))
    monkeypatch.setattr(
        runner,
        "_close_sessions",
        lambda items: _async(cleanup.extend(item.session_id for item in items) or []),
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_review_hold",
        lambda **_kwargs: _async("expired"),
    )

    result = await runner._run(_args(prepare_review_hold=True))

    output = capsys.readouterr().out
    assert result["status"] == "review_hold_expired"
    assert "provider_disabled_review_hold" in output
    assert "activation_manifest_sha256" in output
    assert "token-1" not in output
    assert cleanup == list(range(1, 11))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_review_hold_claim_preserves_exact_sessions_for_later_resume(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", False)
    monkeypatch.setattr(settings, "public_chat_model_eval_enabled", False)
    monkeypatch.setattr(runner, "REVIEW_HOLD_DIRECTORY", tmp_path)
    monkeypatch.setattr(runner, "_create_sessions", lambda _args: _async(_bindings()))
    monkeypatch.setattr(runner, "_close_sessions", lambda _items: _async([]))
    monkeypatch.setattr(
        runner,
        "_wait_for_review_hold",
        lambda **_kwargs: _async("claimed_for_separately_authorized_resume"),
    )
    args = _args(prepare_review_hold=True)
    plan, _count, _policy = runner._build_plan(args, _bindings())
    expected_digest = runner._canonical_manifest_digest(plan["activation_manifest"])

    result = await runner._run(args)
    hold_path = tmp_path / f"{expected_digest}.json"

    assert result["status"] == "review_hold_claimed_for_separately_authorized_resume"
    assert hold_path.exists()
    assert hold_path.stat().st_mode & 0o777 == 0o600
    claimed, bindings, manifest, payload = runner._load_claimed_review_hold(hold_path)
    try:
        assert [item.session_id for item in bindings] == list(range(1, 11))
        assert manifest == plan["activation_manifest"]
        assert payload["organization_id"] == 7
    finally:
        claimed.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_review_hold_rejection_and_interruption_cleanup(monkeypatch, tmp_path):
    cleanup = []
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", False)
    monkeypatch.setattr(settings, "public_chat_model_eval_enabled", False)
    monkeypatch.setattr(runner, "REVIEW_HOLD_DIRECTORY", tmp_path)
    monkeypatch.setattr(runner, "_create_sessions", lambda _args: _async(_bindings()))
    monkeypatch.setattr(
        runner,
        "_close_sessions",
        lambda items: _async(cleanup.extend(item.session_id for item in items) or []),
    )
    outcomes = iter(("rejected", KeyboardInterrupt()))

    async def wait(**_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(runner, "_wait_for_review_hold", wait)

    rejected = await runner._run(_args(prepare_review_hold=True))
    interrupted = await runner._run(_args(prepare_review_hold=True))

    assert rejected["status"] == "review_hold_rejected"
    assert interrupted["status"] == "review_hold_cancelled"
    assert cleanup == list(range(1, 11)) * 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_resume_claims_exact_hold_before_live_gates(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", True)
    monkeypatch.setattr(settings, "public_chat_model_eval_enabled", True)
    monkeypatch.setattr(runner, "REVIEW_HOLD_DIRECTORY", tmp_path)
    prepared_args = _args(prepare_review_hold=True)
    plan, _count, _policy = runner._build_plan(prepared_args, _bindings())
    hold_path = runner._write_review_hold(
        arguments=prepared_args,
        bindings=_bindings(),
        manifest=plan["activation_manifest"],
    )
    resume_args = _args(
        execute_live=True,
        resume_review_hold=hold_path,
    )
    resume_args.expires_at = prepared_args.expires_at
    resume_args.count_evidence_expires_at = prepared_args.count_evidence_expires_at
    resume_args.all_in_evidence_expires_at = prepared_args.all_in_evidence_expires_at
    cleaned = []
    events = []
    monkeypatch.setattr(
        runner,
        "_close_sessions",
        lambda items: _async(cleaned.extend(item.session_id for item in items) or []),
    )
    monkeypatch.setattr(runner.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(runner.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr(
        runner, "_require_approval", lambda *_a, **_k: _async(_approved_runtime())
    )
    monkeypatch.setattr(runner, "_require_live_authorization", _live_authorized)

    @contextmanager
    def secret():
        yield "synthetic-never-networked-key"

    class Client:
        async def aclose(self):
            return None

    @asynccontextmanager
    async def lifecycle(**_kwargs):
        yield object()

    async def execute(bindings, **_kwargs):
        events.extend(item.session_id for item in bindings)
        return {"turns_attempted": 50}

    monkeypatch.setattr(runner, "operator_entered_api_key", secret)
    monkeypatch.setattr(runner, "NoRetryHttpClient", Client)
    monkeypatch.setattr(runner, "installed_development_evaluation_provider", lifecycle)
    monkeypatch.setattr(runner, "_execute_turns", execute)

    result = await runner._run(resume_args)

    assert result == {"turns_attempted": 50}
    assert events == list(range(1, 11))
    assert cleaned == list(range(1, 11))
    assert list(tmp_path.iterdir()) == []


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

    async def live_authorized(*_args, **_kwargs):
        events.append("live-authorization")
        return SimpleNamespace(decision="authorized")

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
    monkeypatch.setattr(runner, "_require_live_authorization", live_authorized)
    monkeypatch.setattr(runner, "operator_entered_api_key", secret)
    monkeypatch.setattr(runner, "NoRetryHttpClient", Client)
    monkeypatch.setattr(runner, "installed_development_evaluation_provider", lifecycle)
    monkeypatch.setattr(runner, "_execute_turns", execute)

    result = await runner._run(_args(execute_live=True))
    assert result == {"turns_attempted": 50}
    assert events == [
        "approval",
        "live-authorization",
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
async def test_release_approval_without_live_authorization_never_prompts(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", True)
    monkeypatch.setattr(settings, "public_chat_model_eval_enabled", True)
    monkeypatch.setattr(runner, "_create_sessions", lambda _args: _async(_bindings()))
    monkeypatch.setattr(runner, "_close_sessions", lambda _items: _async(None))
    monkeypatch.setattr(runner.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(runner.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr(runner, "_require_approval", lambda *_a, **_k: _async(_approved_runtime()))

    async def missing(*_args, **_kwargs):
        raise EvaluationBlockedError("latest live evaluation authorization is not authorized")

    monkeypatch.setattr(runner, "_require_live_authorization", missing)
    monkeypatch.setattr(
        runner,
        "operator_entered_api_key",
        lambda: (_ for _ in ()).throw(AssertionError("secret must not be requested")),
    )
    monkeypatch.setattr(
        runner,
        "NoRetryHttpClient",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be built")),
    )
    with pytest.raises(EvaluationBlockedError, match="live evaluation authorization"):
        await runner._run(_args(execute_live=True))


@pytest.mark.asyncio
async def test_approval_rejects_any_nonpromotion_manifest_drift(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    proposal, _count_policy, _policy = runner._build_plan(_args(), _bindings())
    approved = _promote_proposal(proposal["activation_manifest"])
    approved["count_policy"]["evidence_id"] = "different-approved-evidence"
    approved["additional_all_in_reserve_usd"] = 0.003
    approved["combined_reservation_usd"] = 0.0635

    class DB:
        async def scalar(self, _query):
            return SimpleNamespace(
                decision="approved",
                decided_by_subject="authenticated-owner",
                comparison_snapshot={"provider_activation": approved},
            )

    class DBContext:
        async def __aenter__(self):
            return DB()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(runner, "AsyncSessionLocal", DBContext)
    with pytest.raises(
        EvaluationBlockedError,
        match="not approved|differs from the exact printed proposal",
    ):
        await runner._require_approval(
            _args(), _bindings(), proposal["activation_manifest"]
        )


@pytest.mark.asyncio
async def test_latest_rejection_supersedes_older_approval_before_runtime(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    proposal, _count_policy, _policy = runner._build_plan(_args(), _bindings())
    guard_calls = 0

    class DB:
        async def scalar(self, _query):
            return SimpleNamespace(
                decision="rejected",
                decided_by_subject="authenticated-owner",
                comparison_snapshot={},
            )

    class DBContext:
        async def __aenter__(self):
            return DB()

        async def __aexit__(self, *_args):
            return False

    async def guard(*_args, **_kwargs):
        nonlocal guard_calls
        guard_calls += 1

    monkeypatch.setattr(runner, "AsyncSessionLocal", DBContext)
    monkeypatch.setattr(
        runner.PersistedEvaluationActivationGuard, "require", guard
    )
    with pytest.raises(EvaluationBlockedError, match="latest.*not approved"):
        await runner._require_approval(
            _args(), _bindings(), proposal["activation_manifest"]
        )
    assert guard_calls == 0


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

    async def live_authorized(*_args, **_kwargs):
        events.append("live-authorization")
        return SimpleNamespace(decision="authorized")

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
    monkeypatch.setattr(runner, "_require_live_authorization", live_authorized)
    monkeypatch.setattr(runner, "operator_entered_api_key", secret)
    monkeypatch.setattr(runner, "NoRetryHttpClient", Client)
    monkeypatch.setattr(runner, "installed_development_evaluation_provider", lifecycle)
    monkeypatch.setattr(runner, "_execute_turns", interrupted)
    monkeypatch.setattr(runner, "_close_sessions", cleanup)

    with pytest.raises(KeyboardInterrupt):
        await runner._run(_args(execute_live=True))
    assert events == [
        "approval",
        "live-authorization",
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
    monkeypatch.setattr(runner, "_require_live_authorization", _live_authorized)
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
        require_live_authorization=_live_authorized,
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
            require_live_authorization=_live_authorized,
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
            require_live_authorization=_live_authorized,
        )
    assert persisted[0]["succeeded"] is False
    assert persisted[0]["error_category"] == "interrupted"
    assert len(persisted[0]["outcomes"]) == 1


@pytest.mark.asyncio
async def test_live_revocation_during_run_stops_before_next_request(monkeypatch):
    checks = 0
    sends = 0
    persisted = []

    async def authorize(*_args, **_kwargs):
        nonlocal checks
        checks += 1
        if checks > 1:
            raise EvaluationBlockedError(
                "latest live evaluation authorization is not authorized"
            )

    async def send(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        return {
            "status": "ai_active",
            "reply": "synthetic",
            "handoff": False,
            "_model_eval_request_id": "mock-request",
        }

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

    with pytest.raises(EvaluationBlockedError, match="live evaluation authorization"):
        await runner._execute_turns(
            bindings,
            organization_id=7,
            approved_run_id="offline-rag-candidate",
            activation_manifest={"synthetic": True},
            embedding_origin="http://127.0.0.1:3000",
            require_live_authorization=authorize,
        )
    assert checks == 2
    assert sends == 1
    assert persisted[0]["succeeded"] is False
    assert len(persisted[0]["outcomes"]) == 1


async def _async(value):
    return value
