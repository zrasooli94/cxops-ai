"""Phase 1J.1B — evaluation schema tests.

Small, focused checks: valid objects serialize, required fields enforced,
JSON metrics/dimensions accepted, and no PII field accidentally appears in
any read schema.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.evaluation import (
    EvaluationBaselineCreate,
    EvaluationBaselineRead,
    EvaluationCaseCreate,
    EvaluationCaseRead,
    EvaluationIdentitySnapshot,
    EvaluationMetricComparison,
    EvaluationRunComparison,
    EvaluationRunCreate,
    EvaluationRunListResponse,
    EvaluationRunRead,
)

_RUN_PAYLOAD = {
    "run_id": "run-123",
    "target_type": "agent",
    "model": "gpt-test",
    "embedding_model": "text-embed-3",
    "agent_decision_version": "2",
    "tool_policy_version": 1,
}

_CASE_PAYLOAD = {
    "case_id": "case-1",
    "case_type": "agent",
    "expected": {"expected_action": "route"},
    "actual": {"actual_action": "route"},
    "dimensions": {"action_pass": True},
    "input": {"topic": "billing", "language": "en"},
    "latency_ms": 120.5,
    "total_tokens": 512,
    "estimated_cost_usd": 0.004,
    "fingerprint": "fp-1",
}

_BASELINE_PAYLOAD = {
    "target_type": "rag",
    "version": "1.1.0",
    "model": "gpt-test",
    "pass_rate": 0.92,
    "metrics": {"action_accuracy": 0.92},
    "cases_count": 12,
}

PII_NAMES = {
    "email",
    "customer_email",
    "phone",
    "phone_number",
    "ticket_body",
    "conversation_body",
    "conversation",
    "raw_ticket",
    "secret",
    "api_key",
    "oauth_token",
    "credential",
}


def test_evaluation_run_create_serializes_valid_payload():
    run = EvaluationRunCreate.model_validate(_RUN_PAYLOAD)
    assert run.run_id == "run-123"
    assert run.target_type == "agent"
    assert run.trigger_source == "manual"
    assert run.requested_by_subject is None
    dumped = run.model_dump()
    assert dumped["run_id"] == "run-123"
    assert dumped["trigger_source"] == "manual"


@pytest.mark.parametrize(
    "missing",
    [("run_id",), ("target_type",), ("model",)],
)
def test_evaluation_run_create_requires_core_fields(missing):
    payload = dict(_RUN_PAYLOAD)
    del payload[missing[0]]
    with pytest.raises(ValidationError):
        EvaluationRunCreate.model_validate(payload)


def test_evaluation_run_create_rejects_unknown_pii_fields():
    payload = dict(_RUN_PAYLOAD)
    payload["customer_email"] = "user@example.com"
    with pytest.raises(ValidationError):
        EvaluationRunCreate.model_validate(payload)


def test_evaluation_run_read_serializes_and_has_no_pii():
    run = EvaluationRunRead(
        id=1,
        run_id="run-123",
        organization_id=42,
        target_type="agent",
        status="queued",
        model="gpt-test",
        metrics={"latency_p50_ms": 180},
        created_at=datetime(2026, 9, 23, tzinfo=UTC),
    )
    assert run.metrics == {"latency_p50_ms": 180}
    assert run.pass_rate is None
    assert EvaluationRunRead.model_fields.keys() & PII_NAMES == set()


def test_evaluation_case_create_accepts_json_payloads():
    case = EvaluationCaseCreate.model_validate(_CASE_PAYLOAD)
    assert case.expected == {"expected_action": "route"}
    assert case.dimensions == {"action_pass": True}
    assert case.input == {"topic": "billing", "language": "en"}
    assert case.latency_ms == 120.5
    assert case.total_tokens == 512
    assert case.estimated_cost_usd == 0.004


def test_evaluation_case_create_rejects_negative_cost_and_unknown_fields():
    payload = dict(_CASE_PAYLOAD)
    payload["estimated_cost_usd"] = -1.0
    with pytest.raises(ValidationError):
        EvaluationCaseCreate.model_validate(payload)

    payload = dict(_CASE_PAYLOAD)
    payload["phone"] = "+1-555-0100"
    with pytest.raises(ValidationError):
        EvaluationCaseCreate.model_validate(payload)


def test_evaluation_case_read_defaults_and_no_pii():
    case = EvaluationCaseRead(
        id=1,
        run_id=7,
        organization_id=42,
        case_id="case-1",
        case_type="agent",
        created_at=datetime(2026, 9, 23, tzinfo=UTC),
    )
    assert case.expected == {}
    assert case.dimensions == {}
    assert case.latency_ms is None
    assert case.total_tokens is None
    assert EvaluationCaseRead.model_fields.keys() & PII_NAMES == set()


def test_evaluation_baseline_create_validates_ranges():
    baseline = EvaluationBaselineCreate.model_validate(_BASELINE_PAYLOAD)
    assert baseline.pass_rate == 0.92
    assert baseline.metrics == {"action_accuracy": 0.92}
    assert baseline.cases_count == 12

    with pytest.raises(ValidationError):
        EvaluationBaselineCreate.model_validate({**_BASELINE_PAYLOAD, "pass_rate": 1.5})
    with pytest.raises(ValidationError):
        EvaluationBaselineCreate.model_validate({**_BASELINE_PAYLOAD, "cases_count": -1})


def test_evaluation_baseline_read_and_no_pii():
    baseline = EvaluationBaselineRead(
        id=1,
        organization_id=42,
        target_type="rag",
        version="1.1.0",
        model="gpt-test",
        metrics={"action_accuracy": 0.92},
        cases_count=12,
        promoted=False,
        created_at=datetime(2026, 9, 23, tzinfo=UTC),
    )
    assert baseline.promoted is False
    assert baseline.pass_rate is None
    assert EvaluationBaselineRead.model_fields.keys() & PII_NAMES == set()


def test_evaluation_run_list_response_serializes():
    run = EvaluationRunRead.model_validate(
        {
            **{k: v for k, v in _RUN_PAYLOAD.items() if k in {"run_id", "target_type", "model"}},
            "id": 1,
            "organization_id": 42,
            "status": "succeeded",
            "pass_rate": 0.9,
            "metrics": {"action_accuracy": 0.9},
            "created_at": datetime(2026, 9, 23, tzinfo=UTC),
        }
    )
    response = EvaluationRunListResponse(items=[run], total=1, limit=10, offset=0)
    dumped = response.model_dump()
    assert dumped["total"] == 1
    assert dumped["limit"] == 10
    assert dumped["offset"] == 0
    assert dumped["items"][0]["run_id"] == "run-123"


def test_evaluation_run_comparison_serializes():
    identity = EvaluationIdentitySnapshot(
        model="gpt-test",
        embedding_model="text-embed-3",
        agent_decision_version="2",
        tool_policy_version=1,
        corpus_revision={"documents": 7},
    )
    comparison = EvaluationRunComparison(
        target_type="rag",
        candidate_pass_rate=0.9,
        baseline_pass_rate=0.8,
        pass_rate_delta=0.1,
        metrics=[
            EvaluationMetricComparison(
                metric="retrieval_accuracy",
                baseline=0.8,
                candidate=0.9,
                delta=0.1,
                direction="improved",
            ),
            EvaluationMetricComparison(
                metric="refusal_accuracy",
                baseline=0.7,
                candidate=0.7,
                delta=0.0,
                direction="same",
            ),
            EvaluationMetricComparison(
                metric="avg_latency_ms",
                baseline=100.0,
                candidate=120.0,
                delta=20.0,
                direction="regressed",
            ),
        ],
        baseline=identity,
        candidate=identity,
    )
    dumped = comparison.model_dump()
    assert dumped["pass_rate_delta"] == 0.1
    assert [m["direction"] for m in dumped["metrics"]] == ["improved", "same", "regressed"]
    assert dumped["baseline"]["agent_decision_version"] == "2"
    assert dumped["candidate"]["model"] == "gpt-test"
    assert EvaluationRunComparison.model_fields.keys() & PII_NAMES == set()


def test_evaluation_run_comparison_rejects_extra_fields():
    identity = EvaluationIdentitySnapshot(model="gpt-test")
    with pytest.raises(ValidationError):
        EvaluationRunComparison(
            target_type="rag",
            pass_rate_delta=None,
            baseline=identity,
            candidate=identity,
            magic_score=0.9,
        )
