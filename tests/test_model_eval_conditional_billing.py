from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.services import model_eval_conditional_billing as conditional
from app.services.model_eval_activation_plan import OperatorActivationPlan
from app.services.model_eval_billing_evidence import (
    INDEPENDENT_BILLING_EVIDENCE_REGISTRY,
)
from app.services.model_eval_controller import EvaluationBlockedError
from app.services.public_chat_model_eval import validate_activation_manifest


def _proposal(*, expires_at=None, additional=0.00484):
    expiry = expires_at or datetime.now(UTC) + timedelta(minutes=30)
    return OperatorActivationPlan.build(
        environment="development",
        organization_id=17,
        public_chat_session_ids=tuple(range(101, 111)),
        run_id="conditional-billing-test",
        expires_at=expiry,
        count_billing_status="unknown",
        count_cost_reservation_per_attempt_usd=0.00030,
        count_billing_evidence_url=(
            "https://developers.openai.com/api/docs/guides/token-counting"
        ),
        count_billing_evidence_id=conditional.COUNT_EVIDENCE_ID,
        count_billing_evidence_expires_at=expiry,
        additional_all_in_reserve_usd=additional,
        all_in_evidence_id=conditional.ALL_IN_EVIDENCE_ID,
        all_in_evidence_expires_at=expiry,
        conditional_billing_policy_version=(
            conditional.CONDITIONAL_BILLING_POLICY_VERSION
        ),
        frozen_fixture_sha256="a" * 64,
        evaluation_spec_sha256="b" * 64,
        runner_source_sha256="c" * 64,
        reviewed_source_commit="d" * 40,
    ).render()["activation_manifest"]


def _accept(manifest, **overrides):
    acceptance = conditional.conditional_acceptance_request()
    acceptance.update(overrides)
    return conditional.require_conditional_billing_acceptance(
        manifest,
        acceptance=acceptance,
        accepted_by_subject="authenticated-owner",
    )


def test_conditional_policy_is_separate_from_empty_independent_registry():
    assert INDEPENDENT_BILLING_EVIDENCE_REGISTRY == ()
    proposal = _proposal()
    assert proposal["count_policy"]["billing_status_claim"] == "unknown"
    assert (
        proposal["count_policy"]["evidence_verification"]
        == "owner_acceptance_pending_not_independently_verified"
    )
    assert proposal["count_policy"]["reservation_all_attempts_usd"] == 0.03
    assert proposal["additional_all_in_reserve_usd"] == 0.00484
    assert proposal["combined_reservation_usd"] == 0.09534
    assert proposal["conditional_billing_policy"]["remaining_headroom_usd"] == 0.00466


def test_authenticated_acceptance_promotes_but_never_claims_independent_review():
    accepted = _accept(_proposal())
    validated = validate_activation_manifest(accepted)
    assert validated["conditional_billing_policy"]["accepted_by_subject"] == (
        "authenticated-owner"
    )
    assert (
        validated["count_policy"]["evidence_verification"]
        == "owner_accepted_uncertainty_not_independently_verified"
    )
    assert validated["count_policy"]["explicitly_approved"] is True


@pytest.mark.parametrize(
    "acceptance",
    [
        {"accepted": False},
        {"policy_version": "changed"},
        {"support_evidence_sha256": "0" * 64},
        {"owner_acceptance_statement_sha256": "0" * 64},
    ],
)
def test_missing_or_changed_acceptance_fails_closed(acceptance):
    with pytest.raises(EvaluationBlockedError, match="acceptance is invalid"):
        _accept(_proposal(), **acceptance)


def test_expired_or_changed_policy_fails_closed():
    accepted = _accept(_proposal())
    expired = deepcopy(accepted)
    expired["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    expired["count_policy"]["evidence_expires_at"] = expired["expires_at"]
    expired["all_in_evidence_expires_at"] = expired["expires_at"]
    expired["conditional_billing_policy"]["expires_at"] = expired["expires_at"]
    with pytest.raises(EvaluationBlockedError, match="expired|not approved"):
        validate_activation_manifest(expired)

    drift = deepcopy(accepted)
    drift["conditional_billing_policy"]["count_uncertainty_reserve_usd"] = 0.02
    with pytest.raises(EvaluationBlockedError, match="invalid|differs"):
        validate_activation_manifest(drift)


def test_insufficient_headroom_is_rejected_before_acceptance():
    with pytest.raises(ValueError, match="differs from the source-owned policy"):
        _proposal(additional=0.0395)
