import pytest

from app.services.model_eval_budget import ModelEvalBudget, conservative_token_bound


def _budget(**changes):
    values = {
        "max_turns": 50,
        "max_input_tokens_per_turn": 3000,
        "max_output_tokens_per_turn": 500,
        "max_attempts_per_turn": 2,
        "input_cost_per_million": 0.10,
        "output_cost_per_million": 0.50,
        "cost_ceiling_usd": 0.25,
    }
    values.update(changes)
    return ModelEvalBudget(**values)


def test_fifty_turn_plan_is_bounded_by_quarter_dollar():
    assert _budget().validate(50) == pytest.approx(0.055)


def test_turn_and_cost_limits_fail_closed():
    with pytest.raises(ValueError, match="turn count"):
        _budget().validate(51)
    with pytest.raises(ValueError, match="exceeds"):
        _budget(output_cost_per_million=20).validate(50)
    with pytest.raises(ValueError, match="attempts"):
        _budget(max_attempts_per_turn=0).validate(1)


def test_utf8_bound_handles_persian_without_under_counting_bytes():
    assert conservative_token_bound("ساعات کاری") == len("ساعات کاری".encode())
    assert conservative_token_bound("ساعات کاری") > len("ساعات کاری")
