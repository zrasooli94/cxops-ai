from datetime import UTC, datetime, timedelta
from math import inf, nan

import pytest

from app.services.model_eval_activation_plan import (
    ActivationPlanBlocked,
    OperatorActivationPlan,
)


def _plan(**overrides):
    values = {
        "environment": "development",
        "organization_id": 17,
        "public_chat_session_ids": tuple(range(29, 39)),
        "run_id": "controlled-eval-01",
        "expires_at": datetime.now(UTC) + timedelta(minutes=30),
        "additional_all_in_reserve_usd": 0.01,
        "all_in_evidence_id": "synthetic-all-in-evidence",
        "all_in_evidence_expires_at": datetime.now(UTC) + timedelta(minutes=30),
        "all_in_independently_verified": True,
        "frozen_fixture_sha256": "a" * 64,
        "evaluation_spec_sha256": "b" * 64,
        "runner_source_sha256": "c" * 64,
        "reviewed_source_commit": "d" * 40,
    }
    values.update(overrides)
    return OperatorActivationPlan.build(**values)


def test_default_plan_is_no_write_no_network_and_count_blocked():
    rendered = _plan().render()
    assert rendered["ready_to_activate"] is False
    assert rendered["write_performed"] is False
    assert rendered["credential"]["value_read"] is False
    assert "billing is unresolved" in rendered["blockers"][0]
    assert rendered["binding"]["organization_id"] == 17
    assert rendered["activation_manifest"]["public_chat_session_ids"] == list(
        range(29, 39)
    )
    assert (
        rendered["required_release_decision_snapshot"]["provider_activation"]
        == (rendered["activation_manifest"])
    )


def test_billable_count_cost_is_reserved_for_all_attempts_and_must_leave_headroom():
    evidence_expiry = datetime.now(UTC) + timedelta(minutes=30)
    rendered = _plan(
        count_billing_status="billable",
        count_cost_reservation_per_attempt_usd=0.0001,
        count_billing_evidence_url="https://developers.openai.com/example",
        count_billing_evidence_id="synthetic-count-evidence",
        count_billing_evidence_expires_at=evidence_expiry,
    ).render()
    assert rendered["budget"]["count_reservation_usd"] == 0.01
    assert rendered["budget"]["generation_reservation_usd"] == 0.0605
    assert rendered["budget"]["combined_reservation_usd"] == 0.0805
    assert rendered["budget"]["positive_headroom"] is True
    assert rendered["activation_manifest"]["count_policy"] == {
        "endpoint": "https://api.openai.com/v1/responses/input_tokens",
        "model": "gpt-6-luna",
        "region": "standard",
        "billing_status_claim": "billable",
        "evidence_url": "https://developers.openai.com/example",
        "evidence_verification": "operator_supplied_unverified_by_dry_run",
        "evidence_id": "synthetic-count-evidence",
        "evidence_expires_at": evidence_expiry.isoformat(),
        "explicitly_approved": False,
        "reservation_per_attempt_usd": 0.0001,
        "reservation_all_attempts_usd": 0.01,
    }
    assert rendered["activation_manifest"]["combined_reservation_usd"] == 0.0805
    assert "independent verification" in rendered["blockers"][0]

    no_headroom = _plan(
        count_billing_status="billable",
        count_cost_reservation_per_attempt_usd=0.001,
        count_billing_evidence_url="https://developers.openai.com/example",
        count_billing_evidence_id="synthetic-count-evidence",
        count_billing_evidence_expires_at=evidence_expiry,
    ).render()
    assert no_headroom["budget"]["positive_headroom"] is False
    assert "lacks budget headroom" in no_headroom["blockers"][0]


def test_verified_no_charge_count_is_bound_with_zero_reservation():
    expiry = datetime.now(UTC) + timedelta(minutes=30)
    rendered = _plan(
        count_billing_status="no_charge",
        count_billing_evidence_id="case-18041142-reviewed",
        count_billing_evidence_expires_at=expiry,
        count_billing_independently_verified=True,
    ).render()
    count = rendered["activation_manifest"]["count_policy"]
    assert count["billing_status_claim"] == "no_charge"
    assert count["reservation_per_attempt_usd"] == 0
    assert count["evidence_verification"] == "independently_verified"
    assert rendered["budget"]["combined_reservation_usd"] == 0.0705


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "production"},
        {"organization_id": 0},
        {"public_chat_session_ids": (1,)},
        {"run_id": "bad/run"},
        {"expires_at": datetime.now(UTC) + timedelta(minutes=2)},
        {"expires_at": datetime.now(UTC) + timedelta(hours=3)},
        {"count_billing_status": "billable"},
        {
            "count_billing_status": "billable",
            "count_cost_reservation_per_attempt_usd": nan,
            "count_billing_evidence_url": "https://developers.openai.com/example",
        },
        {
            "count_billing_status": "billable",
            "count_cost_reservation_per_attempt_usd": inf,
            "count_billing_evidence_url": "https://developers.openai.com/example",
        },
        {"count_billing_status": "no_charge"},
        {
            "count_billing_status": "no_charge",
            "count_billing_evidence_url": "https://example.com/not-authoritative",
        },
        {
            "count_billing_status": "unresolved",
            "count_cost_reservation_per_attempt_usd": 0.0001,
        },
    ],
)
def test_invalid_or_production_plan_fails_closed(overrides):
    with pytest.raises(ActivationPlanBlocked):
        _plan(**overrides)
