"""Provider-independent hard bounds for an explicitly approved model evaluation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelEvalBudget:
    max_turns: int
    max_input_tokens_per_turn: int
    max_output_tokens_per_turn: int
    max_attempts_per_turn: int
    input_cost_per_million: float
    output_cost_per_million: float
    cost_ceiling_usd: float

    def worst_case_cost_usd(self, turns: int) -> float:
        if turns < 0 or turns > self.max_turns:
            raise ValueError(f"turn count must be between 0 and {self.max_turns}")
        if self.max_attempts_per_turn < 1:
            raise ValueError("max attempts per turn must be at least 1")
        return turns * self.max_attempts_per_turn * (
            self.max_input_tokens_per_turn / 1_000_000 * self.input_cost_per_million
            + self.max_output_tokens_per_turn / 1_000_000 * self.output_cost_per_million
        )

    def validate(self, turns: int) -> float:
        worst_case = self.worst_case_cost_usd(turns)
        if worst_case >= self.cost_ceiling_usd:
            raise ValueError(
                f"worst-case ${worst_case:.6f} leaves no headroom below "
                f"${self.cost_ceiling_usd:.6f} ceiling"
            )
        return worst_case


def conservative_token_bound(text: str) -> int:
    """UTF-8 bytes safely upper-bound BPE tokens for English and Persian."""
    return len(text.encode("utf-8"))
