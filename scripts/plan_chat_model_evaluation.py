"""Plan a bounded synthetic bilingual model evaluation without calling a provider.

This command is intentionally plan-only. A separate, owner-approved change must
wire execution after current provider/model pricing and credentials are approved.
"""

from __future__ import annotations

import json

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
    budget = ModelEvalBudget(
        max_turns=50,
        max_input_tokens_per_turn=3000,
        max_output_tokens_per_turn=500,
        max_attempts_per_turn=2,
        input_cost_per_million=0.125,
        output_cost_per_million=0.50,
        cost_ceiling_usd=0.10,
    )
    worst_case = budget.validate(50)
    expanded = [CASES[index % len(CASES)] for index in range(50)]
    if any(conservative_token_bound(prompt) > budget.max_input_tokens_per_turn for _, prompt, _ in expanded):
        raise SystemExit("case exceeds the conservative input-token bound")
    print(json.dumps({
        "mode": "plan_only_no_provider_calls",
        "model": "gpt-6-luna",
        "credential_state": "not_inspected_or_configured",
        "turns": len(expanded),
        "languages": ["en", "fa"],
        "max_input_tokens_per_turn": budget.max_input_tokens_per_turn,
        "max_output_tokens_per_turn": budget.max_output_tokens_per_turn,
        "max_retries": 1,
        "max_billable_attempts_per_turn": budget.max_attempts_per_turn,
        "max_billable_attempts_total": len(expanded) * budget.max_attempts_per_turn,
        "retry_accounting": "worst case treats every initial request and retry as fully billed",
        "runtime_guards": "mock_transport_contract_only_provider_disconnected",
        "sdk_retries": 0,
        "orchestration_retries": 1,
        "reservation_policy": (
            "reserve the full attempt cost before dispatch; ambiguous timeouts retain "
            "the reservation and stop for reconciliation"
        ),
        "serialized_input_scope": "system + history + retrieval + user + framing",
        "excluded_paid_services": ["embeddings", "grader", "tools", "translation"],
        "service_tier": "default",
        "reasoning_effort": "none",
        "streaming": False,
        "prompt_cache_retention": "30m_max; 12h_rejected_at_this_ceiling",
        "cost_ceiling_usd": budget.cost_ceiling_usd,
        "worst_case_cost_usd_at_supplied_rates": round(worst_case, 6),
        "pricing_requires_owner_verified_current_rates": True,
        "scenario_ids": [case[0] for case in expanded],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
