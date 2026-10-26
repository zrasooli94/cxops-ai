from app.services import model_eval_billing_evidence as evidence
from app.services.model_eval_controller import EvaluationBlockedError


def _manifest():
    return {
        "count_policy": {
            "evidence_verification": "operator_supplied_unverified_by_dry_run",
            "evidence_id": "count-reviewed",
            "evidence_url": "https://developers.openai.com/count-policy",
            "billing_status_claim": "billable",
            "reservation_per_attempt_usd": 0.0001,
        },
        "all_in_evidence_verification": "operator_supplied_unverified_by_dry_run",
        "all_in_evidence_id": "all-in-reviewed",
        "additional_all_in_reserve_usd": 0.00484,
    }


def test_operator_claims_cannot_self_attest_independent_review(monkeypatch):
    monkeypatch.setattr(evidence, "INDEPENDENT_BILLING_EVIDENCE_REGISTRY", ())
    try:
        evidence.require_reviewed_billing_evidence(_manifest())
    except EvaluationBlockedError as exc:
        assert "not independently reviewed" in str(exc)
    else:  # pragma: no cover - explicit fail-closed assertion
        raise AssertionError("unregistered operator evidence was accepted")


def test_exact_server_owned_registry_match_is_promoted(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "INDEPENDENT_BILLING_EVIDENCE_REGISTRY",
        (
            evidence.ReviewedBillingEvidence(
                "count-reviewed",
                "https://developers.openai.com/count-policy",
                "billable",
                0.0001,
                "responses_input_tokens",
            ),
            evidence.ReviewedBillingEvidence(
                "all-in-reviewed",
                "reviewed-source",
                "reserved",
                0.00484,
                "all_in_budget",
            ),
        ),
    )
    promoted = evidence.require_reviewed_billing_evidence(_manifest())
    assert promoted["count_policy"]["evidence_verification"] == "independently_verified"
    assert promoted["count_policy"]["explicitly_approved"] is True
    assert promoted["all_in_evidence_verification"] == "independently_verified"


def test_caller_cannot_submit_preverified_label(monkeypatch):
    manifest = _manifest()
    manifest["count_policy"]["evidence_verification"] = "independently_verified"
    monkeypatch.setattr(evidence, "INDEPENDENT_BILLING_EVIDENCE_REGISTRY", ())
    try:
        evidence.require_reviewed_billing_evidence(manifest)
    except EvaluationBlockedError as exc:
        assert "cannot attest" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("caller verification label was accepted")
