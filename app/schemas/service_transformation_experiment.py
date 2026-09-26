"""Pydantic schemas for Service Transformation experiments (Phase 1O).

The contract is strict: exactly the backend-supported target metrics may be
set, unknown keys are rejected, values are finite and per-kind bounded, and at
least one target is required. Scopes are ``organization`` (a `None` ``scope_key``)
or ``queue`` (a required tenant-owned ``scope_key``); value/ROI metrics are
rejected for queue scope because they are not attributable to a single queue.
``organization_id`` is deliberately absent — tenancy is resolved from the
request's ``CurrentTenant`` and never accepted from the request body.
"""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.service_transformation_experiment import VALID_EXPERIMENT_WINDOWS
from app.services.service_transformation_experiment_engine import (
    QUEUE_SCOPE_EXCLUDED_METRICS,
    RATE_METRICS,
    ROI_METRICS,
    SUPPORTED_TARGET_METRICS,
)

SCOPE_TYPE_ORGANIZATION: Literal["organization"] = "organization"
SCOPE_TYPE_QUEUE: Literal["queue"] = "queue"

EXPECTED_DIRECTION_VALUES = ("increase", "decrease")


class ExperimentHypothesis(BaseModel):
    """What change the experiment is intended to pilot. Informational only —
    the engine never grades the hypothesis, only the numeric deltas."""

    summary: str = Field(min_length=1, max_length=500)
    change_description: str | None = Field(default=None, max_length=2000)
    expected_direction: dict[str, str] | None = Field(default=None)

    model_config = ConfigDict(extra="forbid")

    @field_validator("summary")
    @classmethod
    def _summary_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("hypothesis summary cannot be blank")
        return value

    @field_validator("expected_direction")
    @classmethod
    def _directions_valid(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return value
        for metric, direction in value.items():
            if metric not in SUPPORTED_TARGET_METRICS:
                raise ValueError(
                    f"expected_direction references unsupported metric: {metric}"
                )
            if direction not in EXPECTED_DIRECTION_VALUES:
                raise ValueError(
                    f"expected_direction for {metric} must be "
                    f"one of {sorted(EXPECTED_DIRECTION_VALUES)}"
                )
        return value


class ServiceTransformationExperimentCreate(BaseModel):
    """Create an experiment draft.

    ``baseline_window_days`` is the trailing window used when the baseline is
    later captured; ``measurement_window_days`` is the planned minimum observed
    window checked when the experiment is completed. ``source_scenario_id``
    optionally links a Phase 1N scenario for the projection-variance comparison;
    the backend resolves the trusted scenario row and snapshots it — callers
    never supply projected values.
    """

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    scope_type: Literal["organization", "queue"] = SCOPE_TYPE_ORGANIZATION
    scope_key: str | None = Field(default=None, max_length=100)
    baseline_window_days: int = Field(default=30)
    measurement_window_days: int = Field(default=30)
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    hypothesis: ExperimentHypothesis
    target_metrics: dict[str, float]
    source_scenario_id: int | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name cannot be blank")
        return value

    @field_validator("baseline_window_days", "measurement_window_days")
    @classmethod
    def _window_in_valid_values(cls, value: int) -> int:
        if value not in VALID_EXPERIMENT_WINDOWS:
            raise ValueError(
                f"window must be one of {sorted(VALID_EXPERIMENT_WINDOWS)}"
            )
        return value

    @field_validator("scope_key")
    @classmethod
    def _scope_key_stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("scope_key cannot be blank")
        return value.strip()

    @field_validator("target_metrics")
    @classmethod
    def _targets_valid(
        cls, value: dict[str, float]
    ) -> dict[str, float]:
        if not value:
            raise ValueError("at least one target metric is required")
        unknown = [key for key in value if key not in SUPPORTED_TARGET_METRICS]
        if unknown:
            raise ValueError(
                f"unsupported target metrics: {sorted(unknown)}; supported: "
                f"{sorted(SUPPORTED_TARGET_METRICS)}"
            )
        for name, amount in value.items():
            if not isinstance(amount, (int, float)) or not isfinite(amount):
                raise ValueError(f"target {name} must be a finite number")
            if name in RATE_METRICS and not (0 <= float(amount) <= 100):
                raise ValueError(
                    f"target {name} must be a percentage between 0 and 100"
                )
            if (
                name not in RATE_METRICS
                and name not in ROI_METRICS
                and float(amount) < 0
            ):
                raise ValueError(f"target {name} must be non-negative")
        return {name: float(amount) for name, amount in value.items()}

    @model_validator(mode="after")
    def _scope_and_schedule_consistent(
        self,
    ) -> ServiceTransformationExperimentCreate:
        if self.scope_type == SCOPE_TYPE_QUEUE and not self.scope_key:
            raise ValueError("queue scope requires a scope_key")
        if self.scope_type == SCOPE_TYPE_ORGANIZATION and self.scope_key:
            raise ValueError("organization scope must not carry a scope_key")
        if self.scope_type == SCOPE_TYPE_QUEUE:
            invalid = [
                m for m in self.target_metrics if m in QUEUE_SCOPE_EXCLUDED_METRICS
            ]
            if invalid:
                raise ValueError(
                    f"queue scope cannot target value/ROI metrics: "
                    f"{sorted(invalid)}"
                )
        if (
            self.planned_start_at is not None
            and self.planned_end_at is not None
            and self.planned_end_at <= self.planned_start_at
        ):
            raise ValueError("planned_end_at must be after planned_start_at")
        return self


class ServiceTransformationExperimentRead(BaseModel):
    """Full experiment row. All measurement data (baseline_snapshot,
    observed_outcome, outcome_comparison) is backend-computed and trusted."""

    id: int
    organization_id: int
    name: str
    description: str | None = None
    status: str = "draft"
    scope_type: str = SCOPE_TYPE_ORGANIZATION
    scope_key: str | None = None
    baseline_window_days: int
    measurement_window_days: int
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    actual_started_at: datetime | None = None
    actual_ended_at: datetime | None = None
    hypothesis: dict
    target_metrics: dict[str, Any]
    source_scenario_id: int | None = None
    source_scenario_snapshot: dict | None = None
    baseline_snapshot: dict | None = None
    baseline_captured_at: datetime | None = None
    observed_outcome: dict | None = None
    outcome_comparison: dict | None = None
    measured_at: datetime | None = None
    measurement_status: str | None = None
    comparison_version: str | None = None
    created_by_subject: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)