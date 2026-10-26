"""Server-owned billing evidence registry for controlled model evaluation.

Operator CLI values are proposals, never attestations.  A development release
decision can promote a proposal only when its complete evidence tuple matches
an entry reviewed into this source-owned registry.  The production registry is
intentionally empty until independent evidence exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.model_eval_controller import EvaluationBlockedError


@dataclass(frozen=True)
class ReviewedBillingEvidence:
    evidence_id: str
    evidence_url: str
    billing_status: str
    reservation_per_attempt_usd: float
    scope: str


# Deliberately empty.  Case 18041142 is preserved supporting evidence, but an
# AI Support Agent screenshot alone is not independent proof of count billing.
INDEPENDENT_BILLING_EVIDENCE_REGISTRY: tuple[ReviewedBillingEvidence, ...] = ()


def require_reviewed_billing_evidence(manifest: dict) -> dict:
    """Promote exact registry matches; reject caller-asserted verification."""
    count = manifest.get("count_policy")
    if not isinstance(count, dict):
        raise EvaluationBlockedError("count billing evidence is invalid")
    if (
        count.get("evidence_verification")
        != "operator_supplied_unverified_by_dry_run"
    ):
        raise EvaluationBlockedError(
            "caller-supplied billing evidence cannot attest independent review"
        )
    matches = [
        item
        for item in INDEPENDENT_BILLING_EVIDENCE_REGISTRY
        if item.scope == "responses_input_tokens"
        and item.evidence_id == count.get("evidence_id")
        and item.evidence_url == count.get("evidence_url")
        and item.billing_status == count.get("billing_status_claim")
        and item.reservation_per_attempt_usd
        == count.get("reservation_per_attempt_usd")
    ]
    if len(matches) != 1:
        raise EvaluationBlockedError(
            "count billing evidence is not independently reviewed"
        )
    if (
        manifest.get("all_in_evidence_verification")
        != "operator_supplied_unverified_by_dry_run"
    ):
        raise EvaluationBlockedError(
            "caller-supplied all-in evidence cannot attest independent review"
        )
    all_in_matches = [
        item
        for item in INDEPENDENT_BILLING_EVIDENCE_REGISTRY
        if item.scope == "all_in_budget"
        and item.evidence_id == manifest.get("all_in_evidence_id")
        and item.reservation_per_attempt_usd
        == manifest.get("additional_all_in_reserve_usd")
    ]
    if len(all_in_matches) != 1:
        raise EvaluationBlockedError(
            "all-in billing evidence is not independently reviewed"
        )
    promoted = dict(manifest)
    promoted["count_policy"] = dict(count)
    promoted["count_policy"]["evidence_verification"] = "independently_verified"
    promoted["count_policy"]["explicitly_approved"] = True
    promoted["all_in_evidence_verification"] = "independently_verified"
    return promoted
