"""Pure operator activation planning; never persists approval or reads credentials."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal
from urllib.parse import urlparse

from app.services.model_eval_conditional_billing import (
    CONDITIONAL_BILLING_POLICY,
    CONDITIONAL_BILLING_POLICY_VERSION,
)
from app.services.model_eval_controller import EvaluationPolicy


class ActivationPlanBlocked(ValueError):
    pass


@dataclass(frozen=True)
class OperatorActivationPlan:
    organization_id: int
    public_chat_session_ids: tuple[int, ...]
    run_id: str
    expires_at: str
    count_billing_status: Literal["unresolved", "billable", "no_charge", "unknown"] = "unresolved"
    count_cost_reservation_per_attempt_usd: float | None = None
    count_billing_evidence_url: str | None = None
    count_billing_evidence_id: str | None = None
    count_billing_evidence_expires_at: str | None = None
    count_billing_independently_verified: bool = False
    additional_all_in_reserve_usd: float | None = None
    all_in_evidence_id: str | None = None
    all_in_evidence_expires_at: str | None = None
    all_in_independently_verified: bool = False
    conditional_billing_policy_version: str | None = None
    frozen_fixture_sha256: str = ""
    evaluation_spec_sha256: str = ""
    runner_source_sha256: str = ""
    reviewed_source_commit: str = ""

    @classmethod
    def build(
        cls,
        *,
        environment: str,
        organization_id: int,
        public_chat_session_ids: tuple[int, ...],
        run_id: str,
        expires_at: datetime,
        count_billing_status: str = "unresolved",
        count_cost_reservation_per_attempt_usd: float | None = None,
        count_billing_evidence_url: str | None = None,
        count_billing_evidence_id: str | None = None,
        count_billing_evidence_expires_at: datetime | None = None,
        count_billing_independently_verified: bool = False,
        additional_all_in_reserve_usd: float | None = None,
        all_in_evidence_id: str | None = None,
        all_in_evidence_expires_at: datetime | None = None,
        all_in_independently_verified: bool = False,
        conditional_billing_policy_version: str | None = None,
        frozen_fixture_sha256: str = "",
        evaluation_spec_sha256: str = "",
        runner_source_sha256: str = "",
        reviewed_source_commit: str = "",
    ) -> OperatorActivationPlan:
        now = datetime.now(UTC)
        if environment != "development":
            raise ActivationPlanBlocked("activation planning is development-only")
        if organization_id < 1:
            raise ActivationPlanBlocked("tenant identifier must be positive")
        if (
            len(public_chat_session_ids) != 10
            or len(set(public_chat_session_ids)) != 10
            or any(item < 1 for item in public_chat_session_ids)
        ):
            raise ActivationPlanBlocked(
                "exactly ten unique positive session ids are required"
            )
        if re.fullmatch(r"[A-Za-z0-9-]{1,64}", run_id) is None:
            raise ActivationPlanBlocked("run id is invalid")
        if expires_at.tzinfo is None:
            raise ActivationPlanBlocked("expiry must include a timezone")
        expiry = expires_at.astimezone(UTC)
        if expiry <= now + timedelta(minutes=5) or expiry > now + timedelta(hours=2):
            raise ActivationPlanBlocked("expiry must be 5 minutes to 2 hours from now")
        if count_billing_status not in {
            "unresolved",
            "billable",
            "no_charge",
            "unknown",
        }:
            raise ActivationPlanBlocked("count billing status is invalid")
        if count_billing_status in {"billable", "unknown"}:
            if (
                count_cost_reservation_per_attempt_usd is None
                or count_cost_reservation_per_attempt_usd <= 0
                or not isfinite(count_cost_reservation_per_attempt_usd)
            ):
                raise ActivationPlanBlocked(
                    "billable or unknown counting requires a positive reservation"
                )
        elif count_cost_reservation_per_attempt_usd is not None:
            raise ActivationPlanBlocked(
                "a count reservation is only valid with verified billable status"
            )
        if conditional_billing_policy_version is not None and (
            conditional_billing_policy_version
            != CONDITIONAL_BILLING_POLICY_VERSION
            or count_billing_status != "unknown"
            or count_cost_reservation_per_attempt_usd
            != CONDITIONAL_BILLING_POLICY.count_reservation_per_attempt_usd
            or count_billing_independently_verified
            or all_in_independently_verified
            or additional_all_in_reserve_usd
            != CONDITIONAL_BILLING_POLICY.malaysia_tax_planning_allowance_usd
        ):
            raise ActivationPlanBlocked(
                "conditional billing policy differs from the source-owned policy"
            )
        if count_billing_status == "unresolved":
            if count_billing_evidence_url is not None:
                raise ActivationPlanBlocked(
                    "unresolved count billing must not cite conclusive evidence"
                )
        else:
            parsed_evidence = urlparse(count_billing_evidence_url or "")
            if count_billing_evidence_url and (
                parsed_evidence.scheme != "https"
                or parsed_evidence.hostname
                not in {"developers.openai.com", "help.openai.com"}
            ):
                raise ActivationPlanBlocked(
                    "resolved count billing requires an authoritative OpenAI developer URL"
                )
            if (
                not count_billing_evidence_id
                or count_billing_evidence_expires_at is None
            ):
                raise ActivationPlanBlocked(
                    "resolved count billing requires dated evidence"
                )
            if count_billing_evidence_expires_at is not None and (
                count_billing_evidence_expires_at.tzinfo is None
                or count_billing_evidence_expires_at.astimezone(UTC) <= now
            ):
                raise ActivationPlanBlocked("count billing evidence is expired")
        if additional_all_in_reserve_usd is not None and (
            not isfinite(additional_all_in_reserve_usd)
            or additional_all_in_reserve_usd < 0
        ):
            raise ActivationPlanBlocked("all-in reserve is invalid")
        if all_in_evidence_expires_at is not None and (
            all_in_evidence_expires_at.tzinfo is None
            or all_in_evidence_expires_at.astimezone(UTC) <= now
        ):
            raise ActivationPlanBlocked("all-in evidence is expired")
        for label, digest in (
            ("frozen fixture", frozen_fixture_sha256),
            ("evaluation spec", evaluation_spec_sha256),
            ("runner source", runner_source_sha256),
        ):
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ActivationPlanBlocked(f"{label} SHA-256 is invalid")
        if re.fullmatch(r"[0-9a-f]{40}", reviewed_source_commit) is None:
            raise ActivationPlanBlocked("reviewed source commit is invalid")
        return cls(
            organization_id=organization_id,
            public_chat_session_ids=public_chat_session_ids,
            run_id=run_id,
            expires_at=expiry.isoformat(),
            count_billing_status=count_billing_status,  # type: ignore[arg-type]
            count_cost_reservation_per_attempt_usd=count_cost_reservation_per_attempt_usd,
            count_billing_evidence_url=count_billing_evidence_url,
            count_billing_evidence_id=count_billing_evidence_id,
            count_billing_evidence_expires_at=(
                count_billing_evidence_expires_at.astimezone(UTC).isoformat()
                if count_billing_evidence_expires_at
                else None
            ),
            count_billing_independently_verified=count_billing_independently_verified,
            additional_all_in_reserve_usd=additional_all_in_reserve_usd,
            all_in_evidence_id=all_in_evidence_id,
            all_in_evidence_expires_at=(
                all_in_evidence_expires_at.astimezone(UTC).isoformat()
                if all_in_evidence_expires_at else None
            ),
            all_in_independently_verified=all_in_independently_verified,
            conditional_billing_policy_version=conditional_billing_policy_version,
            frozen_fixture_sha256=frozen_fixture_sha256,
            evaluation_spec_sha256=evaluation_spec_sha256,
            runner_source_sha256=runner_source_sha256,
            reviewed_source_commit=reviewed_source_commit,
        )

    def render(self) -> dict:
        policy = EvaluationPolicy(
            additional_all_in_reserve_usd=self.additional_all_in_reserve_usd or 0,
        )
        count_total = (
            self.count_cost_reservation_per_attempt_usd or 0
        ) * policy.max_attempts
        generation_total = policy.reserved_cost_per_attempt * policy.max_attempts
        combined = (
            generation_total + count_total + (self.additional_all_in_reserve_usd or 0)
        )
        headroom_ok = combined < policy.cost_ceiling_usd
        count_policy = {
            "endpoint": "https://api.openai.com/v1/responses/input_tokens",
            "model": policy.model,
            "region": "standard",
            "billing_status_claim": self.count_billing_status,
            "evidence_url": self.count_billing_evidence_url,
            "evidence_verification": (
                "independently_verified"
                if self.count_billing_independently_verified
                else (
                    "owner_acceptance_pending_not_independently_verified"
                    if self.conditional_billing_policy_version
                    else "operator_supplied_unverified_by_dry_run"
                )
            ),
            "evidence_id": self.count_billing_evidence_id,
            "evidence_expires_at": self.count_billing_evidence_expires_at,
            "explicitly_approved": self.count_billing_independently_verified,
            "reservation_per_attempt_usd": self.count_cost_reservation_per_attempt_usd
            or 0,
            "reservation_all_attempts_usd": round(count_total, 6),
        }
        manifest = {
            "purpose": "controlled_public_chat_model_evaluation",
            "frozen_fixture_sha256": self.frozen_fixture_sha256,
            "evaluation_spec_sha256": self.evaluation_spec_sha256,
            "runner_source_sha256": self.runner_source_sha256,
            "reviewed_source_commit": self.reviewed_source_commit,
            "public_chat_session_ids": list(self.public_chat_session_ids),
            "max_turns": policy.max_turns,
            "max_attempts": policy.max_attempts,
            "cost_ceiling_usd": policy.cost_ceiling_usd,
            "model": policy.model,
            "service_tier": policy.service_tier,
            "max_input_tokens": policy.max_input_tokens,
            "max_output_tokens": policy.max_output_tokens,
            "generation_input_rate_per_million_usd": policy.input_rate_per_million,
            "generation_output_rate_per_million_usd": policy.output_rate_per_million,
            "generation_price_multiplier": policy.price_multiplier,
            "generation_reservation_all_attempts_usd": round(generation_total, 6),
            "additional_all_in_reserve_usd": self.additional_all_in_reserve_usd,
            "all_in_evidence_id": self.all_in_evidence_id,
            "all_in_evidence_expires_at": self.all_in_evidence_expires_at,
            "all_in_evidence_verification": (
                "independently_verified"
                if self.all_in_independently_verified
                else (
                    "owner_acceptance_pending_not_independently_verified"
                    if self.conditional_billing_policy_version
                    else "operator_supplied_unverified_by_dry_run"
                )
            ),
            "conditional_billing_policy": (
                CONDITIONAL_BILLING_POLICY.as_manifest(expires_at=self.expires_at)
                if self.conditional_billing_policy_version
                else None
            ),
            "count_policy": count_policy,
            "combined_reservation_usd": round(combined, 6),
            "expires_at": self.expires_at,
        }
        blockers = [
            "secure restricted credential is not configured by this tool",
            "provider account/model access is not verified",
            "owner execution approval is not recorded",
        ]
        if (
            not self.count_billing_independently_verified
            and not self.conditional_billing_policy_version
        ):
            blockers.insert(
                0, "count billing evidence requires independent verification"
            )
        if (
            not self.count_billing_evidence_id
            or not self.count_billing_evidence_expires_at
        ):
            blockers.insert(
                0, "count billing evidence identity and expiry are required"
            )
        if (
            self.additional_all_in_reserve_usd is None
            or not self.all_in_evidence_id
            or not self.all_in_evidence_expires_at
            or (
                not self.all_in_independently_verified
                and not self.conditional_billing_policy_version
            )
        ):
            blockers.insert(
                0, "verified all-in surcharge, tax, and ancillary reserve is required"
            )
        if self.count_billing_status == "unresolved":
            blockers.insert(0, "authoritative input-token count billing is unresolved")
        if self.conditional_billing_policy_version:
            blockers.insert(
                0,
                "conditional billing uncertainty requires authenticated owner acceptance",
            )
        if not headroom_ok:
            blockers.insert(
                0, "combined count and generation reservation lacks budget headroom"
            )
        return {
            "mode": "operator_dry_run_no_network_no_writes",
            "ready_to_activate": False,
            "binding": {
                "organization_id": self.organization_id,
                "run_id": self.run_id,
                **manifest,
            },
            "activation_manifest": manifest,
            "required_release_decision_snapshot": {
                "provider_activation": manifest,
            },
            "budget": {
                "generation_reservation_usd": round(generation_total, 6),
                "count_reservation_usd": round(count_total, 6),
                "combined_reservation_usd": round(combined, 6),
                "ceiling_usd": policy.cost_ceiling_usd,
                "positive_headroom": headroom_ok,
                "attempts_include_one_explicit_retry_per_turn": True,
                "count_billing_evidence_url": self.count_billing_evidence_url,
            },
            "credential": {
                "environment_variable": "OPENAI_API_KEY",
                "value_read": False,
                "setup": "inject a restricted project credential out-of-band; never pass it as a CLI argument",
            },
            "blockers": blockers,
            "write_performed": False,
            "input": asdict(self),
        }
