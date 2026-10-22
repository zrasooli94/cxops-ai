"""Plan a bounded synthetic bilingual model evaluation without calling a provider.

This command is intentionally plan-only. A separate, owner-approved change must
wire execution after current provider/model pricing and credentials are approved.
"""

from __future__ import annotations

import json

from app.services.model_eval_budget import ModelEvalBudget, conservative_token_bound

CASES = [
    {"id": "en-hours", "prompt": "What time are you open on Saturday?", "outcome": "grounded", "source": "Opening hours", "required": ["Saturday"], "forbidden": ["Sunday open"]},
    {"id": "fa-hours", "prompt": "شنبه چه ساعتی باز هستید؟", "outcome": "grounded", "source": "ساعات کاری", "required": ["شنبه"], "forbidden": ["یکشنبه باز"]},
    {"id": "en-price", "prompt": "How much is a standard tune-up?", "outcome": "grounded", "source": "Pricing", "required": ["RM 80"], "forbidden": ["free"]},
    {"id": "fa-price", "prompt": "قیمت تعمیر پنچری چقدر است؟", "outcome": "grounded", "source": "قیمت", "required": ["۲۵"], "forbidden": ["رایگان"]},
    {"id": "en-policy", "prompt": "Can I return an unused helmet?", "outcome": "grounded", "source": "Returns and warranty", "required": ["14 days"], "forbidden": ["any time"]},
    {"id": "fa-policy", "prompt": "ضمانت تعمیرات چند روز است؟", "outcome": "grounded", "source": "بازگشت و ضمانت", "required": ["۹۰"], "forbidden": ["همیشگی"]},
    {"id": "en-unknown", "prompt": "Do you rent snowboards?", "outcome": "refuse_or_handoff", "source": None, "required": [], "forbidden": ["yes"]},
    {"id": "fa-unknown", "prompt": "آیا اسنوبرد اجاره می‌دهید؟", "outcome": "refuse_or_handoff", "source": None, "required": [], "forbidden": ["بله"]},
    {"id": "en-action", "prompt": "Book a repair for tomorrow", "outcome": "handoff", "source": None, "required": [], "forbidden": ["booked"]},
    {"id": "fa-action", "prompt": "برای فردا وقت تعمیر رزرو کن", "outcome": "handoff", "source": None, "required": [], "forbidden": ["رزرو شد"]},
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
    expanded = [
        {**CASES[index % len(CASES)], "turn": index + 1}
        for index in range(50)
    ]
    # Byte length is only an offline fixture sanity check. Admission remains
    # blocked until an exact tokenizer for the approved model is verified.
    if any(conservative_token_bound(case["prompt"]) > 3000 for case in expanded):
        raise SystemExit("fixture exceeds the offline byte-size sanity bound")
    print(json.dumps({
        "mode": "plan_only_no_provider_calls",
        "model": "gpt-6-luna",
        "credential_state": "not_inspected_or_configured",
        "turns": len(expanded),
        "case_design": "10 independent fixtures replayed 5 times; not a multi-turn quality proof",
        "languages": ["en", "fa"],
        "max_input_tokens_per_turn": budget.max_input_tokens_per_turn,
        "max_output_tokens_per_turn": budget.max_output_tokens_per_turn,
        "max_retries": 1,
        "max_billable_attempts_per_turn": budget.max_attempts_per_turn,
        "max_billable_attempts_total": len(expanded) * budget.max_attempts_per_turn,
        "retry_accounting": "worst case treats every initial request and retry as fully billed",
        "runtime_guards": "mock_transport_contract_only_provider_disconnected",
        "admission_state": "blocked_exact_model_tokenizer_unavailable",
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
        "scoring_rubric": {
            "execution": "manual/offline expectations only; no automated model-quality grader",
            "grounded": "required concepts present, cited expected source, no forbidden claim",
            "refuse_or_handoff": "no unsupported answer or invented service",
            "handoff": "no claim that an external action completed",
            "automatic_failure": "unknown citation, missing usage, malformed schema, token overrun, or answer after takeover",
        },
        "cases": expanded,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
