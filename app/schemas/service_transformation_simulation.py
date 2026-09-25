"""Pydantic schemas for Service Transformation simulation scenarios.

The assumption contract is strict: exactly five named 0–100 percentage targets,
unknown keys rejected, finite values guaranteed, and at least one target must
be supplied. Scenarios are tenant-owned; ``organization_id`` is resolved from
the request's ``CurrentTenant`` and never accepted from the request body.
"""

from datetime import datetime
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.service_transformation_scenario import VALID_SCENARIO_WINDOWS

SIMULATION_ASSUMPTION_KEYS = (
    "autonomous_execution_rate_target",
    "human_approval_rate_target",
    "knowledge_usage_rate_target",
    "reopen_rate_target",
    "sla_breach_reduction_percent",
)


class SimulationAssumptions(BaseModel):
    """Strict 0–100 percentage targets. `None` = dimension not assumed.

    These are scenario assumptions provided by the operator — they are never
    presented as observed facts. Every supplied value must be a finite number
    in ``[0, 100]``; unknown keys and non-finite values are rejected.
    """

    autonomous_execution_rate_target: float | None = Field(default=None)
    human_approval_rate_target: float | None = Field(default=None)
    knowledge_usage_rate_target: float | None = Field(default=None)
    reopen_rate_target: float | None = Field(default=None)
    sla_breach_reduction_percent: float | None = Field(default=None)

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "autonomous_execution_rate_target",
        "human_approval_rate_target",
        "knowledge_usage_rate_target",
        "reopen_rate_target",
        "sla_breach_reduction_percent",
    )
    @classmethod
    def _percentage_is_finite_and_bounded(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if not isfinite(value):
            raise ValueError("assumption must be a finite number")
        if value < 0 or value > 100:
            raise ValueError("assumption must be a percentage between 0 and 100")
        return value

    @model_validator(mode="after")
    def _at_least_one_assumption(self) -> "SimulationAssumptions":
        if all(
            getattr(self, key) is None for key in SIMULATION_ASSUMPTION_KEYS
        ):
            raise ValueError("at least one scenario assumption is required")
        return self


class ServiceTransformationScenarioCreate(BaseModel):
    """Create a scenario draft.

    ``days`` selects the observed-baseline window (7, 30, or 90; default 30).
    ``organization_id`` is deliberately absent: tenancy is resolved from the
    authenticated request context.
    """

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    days: int = Field(default=30)
    assumptions: SimulationAssumptions

    model_config = ConfigDict(extra="forbid")

    @field_validator("days")
    @classmethod
    def _days_in_valid_windows(cls, value: int) -> int:
        if value not in VALID_SCENARIO_WINDOWS:
            raise ValueError(
                f"days must be one of {sorted(VALID_SCENARIO_WINDOWS)}"
            )
        return value

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name cannot be blank")
        return value


class ServiceTransformationScenarioRead(BaseModel):
    id: int
    organization_id: int
    name: str
    description: str | None = None
    window_days: int
    status: str = "draft"
    assumptions: dict
    observed_baseline: dict | None = None
    projected_result: dict | None = None
    formula_version: str | None = None
    evaluated_at: datetime | None = None
    created_by_subject: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ServiceTransformationScenarioEvaluateResponse(BaseModel):
    """Full evaluation payload, keeping observed and projected data distinct.

    ``baseline`` is the read-only observed transformation snapshot captured at
    evaluation time; ``assumptions`` are the operator-supplied targets;
    ``projected`` is the deterministic engine output; ``deltas`` reports the
    projected-vs-observed differences; ``warnings`` lists bounded modelling
    limitations. ``measurement_status`` reports the value/ROI projection state
    (``pricing_not_configured``, ``insufficient_sample``, or ``measured``).
    """

    scenario: ServiceTransformationScenarioRead
    baseline: dict
    assumptions: dict
    projected: dict
    deltas: dict
    warnings: list[str]
    measurement_status: str = "measured"

    model_config = ConfigDict(from_attributes=True)