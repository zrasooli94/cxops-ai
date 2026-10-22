"""Pure operator activation planning; never persists approval or reads credentials."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal
from urllib.parse import urlparse

from app.services.model_eval_controller import EvaluationPolicy


class ActivationPlanBlocked(ValueError):
    pass


@dataclass(frozen=True)
class OperatorActivationPlan:
    organization_id: int
    public_chat_session_id: int
    run_id: str
    expires_at: str
    count_billing_status: Literal["unresolved", "billable", "no_charge"] = "unresolved"
    count_cost_reservation_per_attempt_usd: float | None = None
    count_billing_evidence_url: str | None = None

    @classmethod
    def build(
        cls,
        *,
        environment: str,
        organization_id: int,
        public_chat_session_id: int,
        run_id: str,
        expires_at: datetime,
        count_billing_status: str = "unresolved",
        count_cost_reservation_per_attempt_usd: float | None = None,
        count_billing_evidence_url: str | None = None,
    ) -> OperatorActivationPlan:
        now = datetime.now(UTC)
        if environment != "development":
            raise ActivationPlanBlocked("activation planning is development-only")
        if organization_id < 1 or public_chat_session_id < 1:
            raise ActivationPlanBlocked("tenant and session identifiers must be positive")
        if re.fullmatch(r"[A-Za-z0-9-]{1,64}", run_id) is None:
            raise ActivationPlanBlocked("run id is invalid")
        if expires_at.tzinfo is None:
            raise ActivationPlanBlocked("expiry must include a timezone")
        expiry = expires_at.astimezone(UTC)
        if expiry <= now + timedelta(minutes=5) or expiry > now + timedelta(hours=2):
            raise ActivationPlanBlocked("expiry must be 5 minutes to 2 hours from now")
        if count_billing_status not in {"unresolved", "billable", "no_charge"}:
            raise ActivationPlanBlocked("count billing status is invalid")
        if count_billing_status == "billable":
            if (
                count_cost_reservation_per_attempt_usd is None
                or count_cost_reservation_per_attempt_usd <= 0
                or not isfinite(count_cost_reservation_per_attempt_usd)
            ):
                raise ActivationPlanBlocked(
                    "billable counting requires a positive per-attempt reservation"
                )
        elif count_cost_reservation_per_attempt_usd is not None:
            raise ActivationPlanBlocked(
                "a count reservation is only valid with verified billable status"
            )
        if count_billing_status == "unresolved":
            if count_billing_evidence_url is not None:
                raise ActivationPlanBlocked(
                    "unresolved count billing must not cite conclusive evidence"
                )
        else:
            parsed_evidence = urlparse(count_billing_evidence_url or "")
            if (
                parsed_evidence.scheme != "https"
                or parsed_evidence.hostname != "developers.openai.com"
            ):
                raise ActivationPlanBlocked(
                    "resolved count billing requires an authoritative OpenAI developer URL"
                )
        return cls(
            organization_id=organization_id,
            public_chat_session_id=public_chat_session_id,
            run_id=run_id,
            expires_at=expiry.isoformat(),
            count_billing_status=count_billing_status,  # type: ignore[arg-type]
            count_cost_reservation_per_attempt_usd=count_cost_reservation_per_attempt_usd,
            count_billing_evidence_url=count_billing_evidence_url,
        )

    def render(self) -> dict:
        policy = EvaluationPolicy()
        count_total = (
            (self.count_cost_reservation_per_attempt_usd or 0) * policy.max_attempts
        )
        generation_total = policy.reserved_cost_per_attempt * policy.max_attempts
        combined = generation_total + count_total
        headroom_ok = combined < policy.cost_ceiling_usd
        count_policy = {
            "billing_status_claim": self.count_billing_status,
            "evidence_url": self.count_billing_evidence_url,
            "evidence_verification": "operator_supplied_unverified_by_dry_run",
            "reservation_per_attempt_usd": self.count_cost_reservation_per_attempt_usd,
            "reservation_all_attempts_usd": round(count_total, 6),
        }
        manifest = {
            "purpose": "controlled_public_chat_model_evaluation",
            "public_chat_session_id": self.public_chat_session_id,
            "max_turns": policy.max_turns,
            "max_attempts": policy.max_attempts,
            "cost_ceiling_usd": policy.cost_ceiling_usd,
            "model": policy.model,
            "service_tier": policy.service_tier,
            "max_input_tokens": policy.max_input_tokens,
            "max_output_tokens": policy.max_output_tokens,
            "count_policy": count_policy,
            "combined_reservation_usd": round(combined, 6),
            "expires_at": self.expires_at,
        }
        blockers = [
            "count billing evidence requires independent authoritative verification",
            "secure restricted credential is not configured by this tool",
            "provider account/model access is not verified",
            "owner execution approval is not recorded",
        ]
        if self.count_billing_status == "unresolved":
            blockers.insert(0, "authoritative input-token count billing is unresolved")
        if not headroom_ok:
            blockers.insert(0, "combined count and generation reservation lacks budget headroom")
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
