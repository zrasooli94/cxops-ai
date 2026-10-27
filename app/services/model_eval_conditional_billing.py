"""Source-owned conditional billing policy for one bounded development run.

This module does not verify provider billing. It permits an authenticated owner
to accept a specifically bounded uncertainty allocation while preserving the
separate independent-evidence path. The checked-in policy is disabled until an
``evaluation.manage`` release decision records the exact acceptance statement.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.model_eval_controller import EvaluationBlockedError

CONDITIONAL_BILLING_POLICY_VERSION = "cxops-count-uncertainty-v1"
PAUL_SUPPORT_EVIDENCE_SHA256 = (
    "2096759e3e6e2dfa5842f393810536086466abd43d6dc204f070479fe346c63d"
)
ORIGINAL_CASE_EVIDENCE_SHA256 = (
    "ec6aeba1c797adeab058174b17ef76dcb4f01281e0d98ec0f6958561abf624bf"
)
COUNT_UNCERTAINTY_RESERVE_USD = 0.03000
COUNT_UNCERTAINTY_PER_ATTEMPT_USD = 0.00030
MALAYSIA_TAX_PLANNING_ALLOWANCE_USD = 0.00484
GENERATION_PLANNING_RESERVE_USD = 0.06050
COMBINED_PLANNING_RESERVE_USD = 0.09534
REMAINING_HEADROOM_USD = 0.00466
COUNT_EVIDENCE_ID = f"paul-support-2026-10-27-sha256:{PAUL_SUPPORT_EVIDENCE_SHA256}"
ALL_IN_EVIDENCE_ID = "malaysia-owner-planning-assumptions-2026-10-27"

OWNER_ACCEPTANCE_STATEMENT = (
    "I accept, for this exact development evaluation manifest only, that the "
    "billing of POST /v1/responses/input_tokens remains unknown; that the "
    "US$0.03000 count uncertainty allocation is a local risk allowance rather "
    "than verified pricing or a provider-side ceiling; that US$0.00484 is the "
    "single Malaysia tax planning allowance; and that the local US$0.10 ledger "
    "cannot guarantee the final provider invoice."
)
OWNER_ACCEPTANCE_STATEMENT_SHA256 = hashlib.sha256(
    OWNER_ACCEPTANCE_STATEMENT.encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class ConditionalBillingPolicy:
    version: str = CONDITIONAL_BILLING_POLICY_VERSION
    support_evidence_sha256: str = PAUL_SUPPORT_EVIDENCE_SHA256
    original_case_evidence_sha256: str = ORIGINAL_CASE_EVIDENCE_SHA256
    count_uncertainty_reserve_usd: float = COUNT_UNCERTAINTY_RESERVE_USD
    count_reservation_per_attempt_usd: float = COUNT_UNCERTAINTY_PER_ATTEMPT_USD
    malaysia_tax_planning_allowance_usd: float = MALAYSIA_TAX_PLANNING_ALLOWANCE_USD
    generation_planning_reserve_usd: float = GENERATION_PLANNING_RESERVE_USD
    combined_planning_reserve_usd: float = COMBINED_PLANNING_RESERVE_USD
    remaining_headroom_usd: float = REMAINING_HEADROOM_USD
    owner_acceptance_statement_sha256: str = OWNER_ACCEPTANCE_STATEMENT_SHA256

    def as_manifest(self, *, expires_at: str) -> dict[str, Any]:
        return {
            "mode": "owner_accepted_billing_uncertainty",
            "policy_version": self.version,
            "support_evidence_sha256": self.support_evidence_sha256,
            "original_case_evidence_sha256": self.original_case_evidence_sha256,
            "count_charge_status": "unknown",
            "count_uncertainty_reserve_usd": self.count_uncertainty_reserve_usd,
            "count_reservation_per_attempt_usd": self.count_reservation_per_attempt_usd,
            "malaysia_tax_planning_allowance_usd": self.malaysia_tax_planning_allowance_usd,
            "generation_planning_reserve_usd": self.generation_planning_reserve_usd,
            "combined_planning_reserve_usd": self.combined_planning_reserve_usd,
            "remaining_headroom_usd": self.remaining_headroom_usd,
            "owner_acceptance_statement_sha256": self.owner_acceptance_statement_sha256,
            "owner_acceptance_status": "pending_authenticated_acceptance",
            "accepted_by_subject": None,
            "expires_at": expires_at,
        }


CONDITIONAL_BILLING_POLICY = ConditionalBillingPolicy()


def conditional_acceptance_request() -> dict[str, Any]:
    """Return the exact non-secret fields an authenticated owner must submit."""
    return {
        "accepted": True,
        "policy_version": CONDITIONAL_BILLING_POLICY.version,
        "support_evidence_sha256": CONDITIONAL_BILLING_POLICY.support_evidence_sha256,
        "owner_acceptance_statement_sha256": (
            CONDITIONAL_BILLING_POLICY.owner_acceptance_statement_sha256
        ),
    }


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def require_conditional_billing_acceptance(
    manifest: dict[str, Any],
    *,
    acceptance: dict[str, Any],
    accepted_by_subject: str,
) -> dict[str, Any]:
    """Promote the exact source-owned proposal after authenticated acceptance."""
    if not accepted_by_subject.strip():
        raise EvaluationBlockedError("authenticated owner acceptance is required")
    if acceptance != conditional_acceptance_request():
        raise EvaluationBlockedError("conditional billing acceptance is invalid")
    conditional = manifest.get("conditional_billing_policy")
    expires_at = manifest.get("expires_at")
    if not isinstance(conditional, dict) or not isinstance(expires_at, str):
        raise EvaluationBlockedError("conditional billing policy is missing")
    expected = CONDITIONAL_BILLING_POLICY.as_manifest(expires_at=expires_at)
    if _canonical(conditional) != _canonical(expected):
        raise EvaluationBlockedError("conditional billing policy differs from source")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        raise EvaluationBlockedError("conditional billing policy expiry is invalid") from None
    if expiry.tzinfo is None or expiry.astimezone(UTC) <= datetime.now(UTC):
        raise EvaluationBlockedError("conditional billing acceptance is expired")
    count = manifest.get("count_policy")
    if (
        not isinstance(count, dict)
        or count.get("billing_status_claim") != "unknown"
        or count.get("evidence_verification")
        != "owner_acceptance_pending_not_independently_verified"
        or count.get("reservation_per_attempt_usd")
        != COUNT_UNCERTAINTY_PER_ATTEMPT_USD
        or count.get("reservation_all_attempts_usd")
        != COUNT_UNCERTAINTY_RESERVE_USD
        or count.get("evidence_id") != COUNT_EVIDENCE_ID
        or manifest.get("additional_all_in_reserve_usd")
        != MALAYSIA_TAX_PLANNING_ALLOWANCE_USD
        or manifest.get("all_in_evidence_id") != ALL_IN_EVIDENCE_ID
        or manifest.get("combined_reservation_usd")
        != COMBINED_PLANNING_RESERVE_USD
    ):
        raise EvaluationBlockedError("conditional billing budget differs from policy")
    promoted = deepcopy(manifest)
    promoted_count = promoted["count_policy"]
    promoted_count["evidence_verification"] = (
        "owner_accepted_uncertainty_not_independently_verified"
    )
    promoted_count["explicitly_approved"] = True
    promoted["all_in_evidence_verification"] = (
        "owner_accepted_planning_assumption_not_independently_verified"
    )
    promoted_conditional = promoted["conditional_billing_policy"]
    promoted_conditional["owner_acceptance_status"] = "accepted_by_authenticated_subject"
    promoted_conditional["accepted_by_subject"] = accepted_by_subject
    return promoted


def require_current_conditional_billing_manifest(manifest: dict[str, Any]) -> None:
    """Reject expired, revoked-by-drift, or self-asserted conditional manifests."""
    conditional = manifest.get("conditional_billing_policy")
    if not isinstance(conditional, dict):
        raise EvaluationBlockedError("conditional billing policy is missing")
    expected = CONDITIONAL_BILLING_POLICY.as_manifest(
        expires_at=str(conditional.get("expires_at", ""))
    )
    expected["owner_acceptance_status"] = "accepted_by_authenticated_subject"
    expected["accepted_by_subject"] = conditional.get("accepted_by_subject")
    if (
        not str(conditional.get("accepted_by_subject", "")).strip()
        or _canonical(conditional) != _canonical(expected)
        or conditional.get("expires_at") != manifest.get("expires_at")
    ):
        raise EvaluationBlockedError("conditional billing acceptance is invalid")
    try:
        expiry = datetime.fromisoformat(
            str(conditional["expires_at"]).replace("Z", "+00:00")
        )
    except ValueError:
        raise EvaluationBlockedError("conditional billing acceptance is invalid") from None
    if expiry.tzinfo is None or expiry.astimezone(UTC) <= datetime.now(UTC):
        raise EvaluationBlockedError("conditional billing acceptance is expired")
