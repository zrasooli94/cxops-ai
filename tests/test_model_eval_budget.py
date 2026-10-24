import pytest

from app.services.model_eval_budget import ModelEvalBudget, conservative_token_bound
from scripts.plan_chat_model_evaluation import (
    CONVERSATIONS,
    expanded_turns,
    mocked_mechanical_replay,
    validate_mechanical_spec,
)


def _budget(**changes):
    values = {
        "max_turns": 50,
        "max_input_tokens_per_turn": 3000,
        "max_output_tokens_per_turn": 500,
        "max_attempts_per_turn": 2,
        "input_cost_per_million": 0.10,
        "output_cost_per_million": 0.50,
        "cost_ceiling_usd": 0.10,
    }
    values.update(changes)
    return ModelEvalBudget(**values)


def test_fifty_turn_plan_is_bounded_at_standard_and_single_uplift_rates():
    assert _budget().validate(50) == pytest.approx(0.055)
    assert _budget(price_multiplier=1.10).validate(50) == pytest.approx(0.0605)


def test_higher_input_rate_exhausts_cap_before_any_uplift():
    with pytest.raises(ValueError, match="no headroom"):
        _budget(input_cost_per_million=0.25).validate(50)


def test_turn_and_cost_limits_fail_closed():
    with pytest.raises(ValueError, match="turn count"):
        _budget().validate(51)
    with pytest.raises(ValueError, match="no headroom"):
        _budget(output_cost_per_million=20).validate(50)
    with pytest.raises(ValueError, match="attempts"):
        _budget(max_attempts_per_turn=0).validate(1)


def test_utf8_bound_handles_persian_without_under_counting_bytes():
    assert conservative_token_bound("ساعات کاری") == len("ساعات کاری".encode())
    assert conservative_token_bound("ساعات کاری") > len("ساعات کاری")


def test_offline_replay_is_ten_linked_conversations_and_exactly_fifty_turns():
    turns = expanded_turns()
    validate_mechanical_spec(turns)
    assert len(CONVERSATIONS) == 10
    assert len(turns) == 50
    assert sum(turn["language"] == "en" for turn in turns) == 25
    assert sum(turn["language"] == "fa" for turn in turns) == 25
    assert all(len(conversation["turns"]) == 5 for conversation in CONVERSATIONS)
    assert all(turn["max_billable_attempts"] == 2 for turn in turns)


def test_every_noninitial_turn_links_full_prior_conversation_history():
    turns = expanded_turns()
    for conversation in CONVERSATIONS:
        linked = [turn for turn in turns if turn["conversation_id"] == conversation["id"]]
        for index, turn in enumerate(linked):
            assert turn["conversation_turn"] == index + 1
            assert turn["prior_turn_ids"] == [item["turn_id"] for item in linked[:index]]
    assert sum(bool(turn["prior_turn_ids"]) for turn in turns) == 40


def test_turns_cover_required_behavior_without_quality_scores():
    turns = expanded_turns()
    behaviors = {turn["expected_behavior"] for turn in turns}
    assert {"grounded_answer", "clarification", "refuse_or_handoff", "handoff", "silence_after_handoff"} <= behaviors
    prompts = " ".join(turn["prompt"] for turn in turns)
    assert "Nova Bike" in prompts and "Trek FX" in prompts
    assert "ignore system rules" in prompts and "نادیده بگیر" in prompts
    assert any(len(turn["required_evidence"]) == 2 for turn in turns)
    assert all(turn["forbidden_claims"] for turn in turns)
    for conversation in CONVERSATIONS:
        behavior_sequence = [turn["expected_behavior"] for turn in conversation["turns"]]
        if "handoff" in behavior_sequence:
            handoff_index = behavior_sequence.index("handoff")
            assert all(
                behavior == "silence_after_handoff"
                for behavior in behavior_sequence[handoff_index + 1:]
            )


def test_mocked_replay_uses_full_history_citations_and_handoff_silence():
    turns = expanded_turns()
    replay = mocked_mechanical_replay(turns)
    assert len(replay) == 50
    for conversation in CONVERSATIONS:
        records = [item for item in replay if item["turn_id"].startswith(f"{conversation['id']}:")]
        assert [item["history_entries_before"] for item in records] == [0, 2, 4, 6, 8]
    grounded = [
        (turn, result) for turn, result in zip(turns, replay, strict=True)
        if turn["expected_behavior"] == "grounded_answer"
    ]
    assert grounded and all(
        result["mocked_citation_ids"] == turn["required_evidence"]
        for turn, result in grounded
    )
    for turn, result in zip(turns, replay, strict=True):
        if turn["expected_behavior"] == "silence_after_handoff":
            assert result["mocked_reply"] is None
            assert result["session_state_after"] == "human_requested"
        assert result["exact_token_admission"] == "blocked_unverified_full_responses_request_count"
