"""Render an operator-only activation dry run without network, secrets, or writes."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from app.services.model_eval_activation_plan import OperatorActivationPlan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expires-at", type=datetime.fromisoformat, required=True)
    parser.add_argument(
        "--count-billing-status",
        choices=("unresolved", "billable", "no_charge"),
        default="unresolved",
    )
    parser.add_argument("--count-cost-reservation-per-attempt-usd", type=float)
    parser.add_argument("--count-billing-evidence-url")
    args = parser.parse_args()
    plan = OperatorActivationPlan.build(
        environment="development",
        organization_id=args.organization_id,
        public_chat_session_id=args.session_id,
        run_id=args.run_id,
        expires_at=args.expires_at,
        count_billing_status=args.count_billing_status,
        count_cost_reservation_per_attempt_usd=(
            args.count_cost_reservation_per_attempt_usd
        ),
        count_billing_evidence_url=args.count_billing_evidence_url,
    )
    print(json.dumps(plan.render(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
