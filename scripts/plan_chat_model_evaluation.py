"""Plan a bounded synthetic bilingual model evaluation without calling a provider.

This command is intentionally plan-only. A separate, owner-approved change must
wire execution after current provider/model pricing and credentials are approved.
"""

from __future__ import annotations

import argparse
import json
import os

from app.services.model_eval_budget import ModelEvalBudget, conservative_token_bound

CASES = [
    ("en-hours", "What time are you open on Saturday?", "Opening hours"),
    ("fa-hours", "شنبه چه ساعتی باز هستید؟", "ساعات کاری"),
    ("en-price", "How much is a standard tune-up?", "Pricing"),
    ("fa-price", "قیمت تعمیر پنچری چقدر است؟", "قیمت"),
    ("en-policy", "Can I return an unused helmet?", "Returns and warranty"),
    ("fa-policy", "ضمانت تعمیرات چند روز است؟", "بازگشت و ضمانت"),
    ("en-unknown", "Do you rent snowboards?", "handoff"),
    ("fa-unknown", "آیا اسنوبرد اجاره می‌دهید؟", "handoff"),
    ("en-action", "Book a repair for tomorrow", "handoff"),
    ("fa-action", "برای فردا وقت تعمیر رزرو کن", "handoff"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=50)
    parser.add_argument("--cost-ceiling-usd", type=float, default=0.25)
    parser.add_argument("--input-rate", type=float, default=0.15)
    parser.add_argument("--output-rate", type=float, default=0.60)
    args = parser.parse_args()
    budget = ModelEvalBudget(
        max_turns=50,
        max_input_tokens_per_turn=3000,
        max_output_tokens_per_turn=500,
        input_cost_per_million=args.input_rate,
        output_cost_per_million=args.output_rate,
        cost_ceiling_usd=args.cost_ceiling_usd,
    )
    worst_case = budget.validate(args.turns)
    expanded = [CASES[index % len(CASES)] for index in range(args.turns)]
    if any(conservative_token_bound(prompt) > budget.max_input_tokens_per_turn for _, prompt, _ in expanded):
        raise SystemExit("case exceeds the conservative input-token bound")
    print(json.dumps({
        "mode": "plan_only_no_provider_calls",
        "model": os.getenv("CHAT_MODEL", "gpt-4o-mini"),
        "credential_configured": bool(os.getenv("OPENAI_API_KEY")),
        "turns": len(expanded),
        "languages": ["en", "fa"],
        "max_input_tokens_per_turn": budget.max_input_tokens_per_turn,
        "max_output_tokens_per_turn": budget.max_output_tokens_per_turn,
        "cost_ceiling_usd": budget.cost_ceiling_usd,
        "worst_case_cost_usd_at_supplied_rates": round(worst_case, 6),
        "pricing_requires_owner_verified_current_rates": True,
        "scenario_ids": [case[0] for case in expanded],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
