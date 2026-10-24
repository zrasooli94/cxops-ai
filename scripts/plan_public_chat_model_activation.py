"""Render an operator-only activation dry run without network, secrets, or writes."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from app.services.model_eval_activation_plan import OperatorActivationPlan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--session-id", type=int, action="append", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expires-at", type=datetime.fromisoformat, required=True)
    parser.add_argument(
        "--count-billing-status",
        choices=("unresolved", "billable", "no_charge"),
        default="unresolved",
    )
    parser.add_argument("--count-cost-reservation-per-attempt-usd", type=float)
    parser.add_argument("--count-billing-evidence-url")
    parser.add_argument("--count-billing-evidence-id")
    parser.add_argument(
        "--count-billing-evidence-expires-at", type=datetime.fromisoformat
    )
    parser.add_argument("--count-billing-independently-verified", action="store_true")
    parser.add_argument("--additional-all-in-reserve-usd", type=float, required=True)
    parser.add_argument("--all-in-evidence-id", required=True)
    parser.add_argument("--all-in-evidence-expires-at", type=datetime.fromisoformat)
    parser.add_argument("--all-in-independently-verified", action="store_true")
    args = parser.parse_args()
    plan = OperatorActivationPlan.build(
        environment="development",
        organization_id=args.organization_id,
        public_chat_session_ids=tuple(args.session_id),
        run_id=args.run_id,
        expires_at=args.expires_at,
        count_billing_status=args.count_billing_status,
        count_cost_reservation_per_attempt_usd=(
            args.count_cost_reservation_per_attempt_usd
        ),
        count_billing_evidence_url=args.count_billing_evidence_url,
        count_billing_evidence_id=args.count_billing_evidence_id,
        count_billing_evidence_expires_at=args.count_billing_evidence_expires_at,
        count_billing_independently_verified=args.count_billing_independently_verified,
        additional_all_in_reserve_usd=args.additional_all_in_reserve_usd,
        all_in_evidence_id=args.all_in_evidence_id,
        all_in_evidence_expires_at=args.all_in_evidence_expires_at,
        all_in_independently_verified=args.all_in_independently_verified,
    )
    print(json.dumps(plan.render(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
