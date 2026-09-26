"""Deterministic outcome-comparison engine for Service Transformation experiments.

This is deliberately a pure, side-effect-free module: it takes the captured
baseline snapshot, the operator-set target metrics, and the measured observed
snapshot and returns a bounded, deterministic comparison plus persisted
limitations. It contains no randomness, no current-time dependence, no LLM
call, and no database or system access.

Semantics (fixed by this module, pinned by tests):

- ``change_from_baseline = observed - baseline`` and
  ``variance_from_target = observed - target``, using the raw numeric for each
  metric: percentage-point differences for rate metrics, raw counts/minutes/
  USD otherwise. Values are never inverted for "good/bad" direction, and the
  engine never labels an outcome as good or bad — ``direction_*`` is a neutral
  factual label (increased/decreased/unchanged/not_applicable).
- ``variance_from_projection`` (vs a linked Phase 1N source scenario's
  projected result) is reported only when the metric semantics match exactly
  and only when the scenario was actually evaluated. A window mismatch is
  reported as a deterministic warning — never normalized/extrapolated.
- The measured outcome is the observed value; the projection remains a
  projection. The engine never presents projected value as realized value.
- ``measurement_status`` is an integrity label with a fixed precedence, never
  a success/failure verdict.

Every completed experiment persists the output of ``compare_outcomes``
verbatim, including its ``limitations``. The non-causal disclaimer is always
present and always first; no text in this module claims or implies causation.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

SERVICE_TRANSFORMATION_EXPERIMENT_VERSION = "1"

METRIC_KIND_RATE = "rate"
METRIC_KIND_COUNT = "count"
METRIC_KIND_MINUTES = "minutes"
METRIC_KIND_AMOUNT = "amount"
METRIC_KIND_ROI = "roi"

# Measurement statuses; the top-level precedence (highest first):
# pricing_unavailable > insufficient_sample > incomplete_window >
# no_observed_activity > measured. ``not_measured`` is the pre-completion
# persisted default and never appears in a comparison.
MEASUREMENT_STATUS_NOT_MEASURED = "not_measured"
MEASUREMENT_STATUS_MEASURED = "measured"
MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE = "insufficient_sample"
MEASUREMENT_STATUS_PRICING_UNAVAILABLE = "pricing_unavailable"
MEASUREMENT_STATUS_NO_OBSERVED_ACTIVITY = "no_observed_activity"
MEASUREMENT_STATUS_INCOMPLETE_WINDOW = "incomplete_window"

# Advisory metadata only: whether a DECREASE (True) or INCREASE (False) is
# normally favourable for the metric. It is never used by the comparison
# arithmetic and never presented as a verdict.
_METRIC_SPECS: dict[str, tuple[str, str, bool]] = {
    "autonomous_execution_rate": (
        METRIC_KIND_RATE,
        "percentage_points",
        False,
    ),
    "human_approval_rate": (METRIC_KIND_RATE, "percentage_points", True),
    "knowledge_usage_rate": (METRIC_KIND_RATE, "percentage_points", False),
    "reopen_rate": (METRIC_KIND_RATE, "percentage_points", True),
    "total_sla_breaches": (METRIC_KIND_COUNT, "count", True),
    "average_first_response_minutes": (METRIC_KIND_MINUTES, "minutes", True),
    "average_resolution_time_minutes": (METRIC_KIND_MINUTES, "minutes", True),
    "estimated_minutes_saved": (METRIC_KIND_MINUTES, "minutes", False),
    "estimated_net_savings_usd": (METRIC_KIND_AMOUNT, "usd", False),
    "roi_percent": (METRIC_KIND_ROI, "percentage", False),
}

SUPPORTED_TARGET_METRICS: tuple[str, ...] = tuple(_METRIC_SPECS)

RATE_METRICS: tuple[str, ...] = tuple(
    name for name, (kind, _, _) in _METRIC_SPECS.items() if kind == METRIC_KIND_RATE
)
VALUE_METRICS: tuple[str, ...] = (
    "estimated_minutes_saved",
    "estimated_net_savings_usd",
    "roi_percent",
)

# Queue-scoped experiments cannot attribute value/ROI metrics, so those target
# metrics are rejected for queue scope (enforced in the API schema too).
QUEUE_SCOPE_EXCLUDED_METRICS: tuple[str, ...] = VALUE_METRICS

# ROI may legitimately be negative (costs exceeding measured savings).
ROI_METRICS: tuple[str, ...] = ("roi_percent",)

# Metric -> path within the persisted baseline/observed snapshot.
SNAPSHOT_METRIC_PATH: dict[str, tuple[str, ...]] = {
    "autonomous_execution_rate": ("rates", "autonomous_execution_rate"),
    "human_approval_rate": ("rates", "human_approval_rate"),
    "knowledge_usage_rate": ("rates", "knowledge_usage_rate"),
    "reopen_rate": ("rates", "reopen_rate"),
    "total_sla_breaches": ("total_sla_breaches",),
    "average_first_response_minutes": ("average_first_response_minutes",),
    "average_resolution_time_minutes": ("average_resolution_time_minutes",),
    "estimated_minutes_saved": ("value", "estimated_minutes_saved"),
    "estimated_net_savings_usd": ("value", "estimated_net_savings_usd"),
    "roi_percent": ("value", "roi_percent"),
}

# Metric -> path within a Phase 1N scenario's persisted ``projected`` result.
# Average first response/resolution time has no scenario projection.
PROJECTED_METRIC_PATH: dict[str, tuple[str, ...]] = {
    "autonomous_execution_rate": ("autonomous_execution_rate_percent",),
    "human_approval_rate": ("human_approval_rate_percent",),
    "knowledge_usage_rate": ("knowledge_usage_rate_percent",),
    "reopen_rate": ("reopen_rate_percent",),
    "total_sla_breaches": ("total_sla_breaches",),
    "estimated_minutes_saved": ("value", "estimated_minutes_saved"),
    "estimated_net_savings_usd": ("value", "estimated_net_savings_usd"),
    "roi_percent": ("value", "roi_percent"),
}

# Persisted, fixed limitation strings. The non-causal disclaimer is always
# present and always first so the outcome_comparison is never shown without it.
LIMITATION_NOT_CAUSAL = (
    "Observed improvement within an experiment window does not establish that "
    "the intervention caused the improvement."
)
LIMITATION_NOT_A_TRIAL = (
    "Experiment outcome measurements are calculated from live tenant telemetry "
    "over the defined window and are not a randomized controlled trial; "
    "attribution to the intervention cannot be claimed."
)
LIMITATION_NO_STATISTICAL_CLAIM = (
    "The comparison expresses observed, baseline, target, and projected values; "
    "it does not assert statistical significance, confidence, or causality."
)
LIMITATION_PROJECTION_COVERAGE = (
    "Projection variance is reported only for metrics the scenario engine "
    "projects; average first-response and resolution time have no scenario "
    "projection and are reported without one."
)
LIMITATION_INCOMPLETE_WINDOW = (
    "The observed window is shorter than the planned measurement window; "
    "partial-window results are reported and flagged, never normalized or "
    "extrapolated."
)

_ALWAYS_LIMITATIONS: tuple[str, ...] = (
    LIMITATION_NOT_CAUSAL,
    LIMITATION_NOT_A_TRIAL,
    LIMITATION_NO_STATISTICAL_CLAIM,
    LIMITATION_PROJECTION_COVERAGE,
)

_ROUND = 2


def _validated_targets(targets: dict[str, Any]) -> dict[str, float]:
    for name, value in targets.items():
        if name not in _METRIC_SPECS:
            raise ValueError(f"unsupported target metric: {name}")
        if not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"target {name} must be a finite number")
        kind = _METRIC_SPECS[name][0]
        if kind in (METRIC_KIND_COUNT, METRIC_KIND_MINUTES, METRIC_KIND_AMOUNT):
            if value < 0:
                raise ValueError(f"target {name} must be non-negative")
        elif kind == METRIC_KIND_RATE and (value < 0 or value > 100):
            raise ValueError(f"target {name} must be a percentage in [0, 100]")
    return {name: float(value) for name, value in targets.items()}


def _value_at_path(snapshot: dict, path: tuple[str, ...]) -> Any | None:
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), _ROUND)


def _direction(value: float | None) -> str:
    if value is None:
        return "not_applicable"
    if value > 0:
        return "increased"
    if value < 0:
        return "decreased"
    return "unchanged"


def _default_projected_value(
    projected: dict,
    metric: str,
) -> float | None:
    path = PROJECTED_METRIC_PATH.get(metric)
    if path is None:
        return None
    value = _value_at_path(projected, path)
    if value is None:
        return None
    return _rounded(float(value))


def _observed_activity(observed: dict) -> bool:
    """Whether the observed window recorded any tenant activity at all.

    Activity is drawn from the raw Phase 1L-derived counters (agent runs,
    resolved tickets, first responses, reopen events). Rates / value derived
    fields are never the source of truth for this check.
    """
    keys = ("agent_runs", "tickets_resolved", "first_responses", "reopen_events")
    return any(_value_at_path(observed, (key,)) is not None for key in keys)


def _window_incomplete(
    observed: dict,
    planned_measurement_days: int,
) -> bool:
    elapsed = _value_at_path(observed, ("window_days",))
    if elapsed is None:
        return False
    return float(elapsed) < planned_measurement_days


def _metric_value_status(
    metric: str,
    observed_value: float | None,
    value_measurement_status: str | None,
) -> str:
    if observed_value is not None:
        return MEASUREMENT_STATUS_MEASURED
    if metric in VALUE_METRICS:
        if value_measurement_status == "pricing_unavailable":
            return MEASUREMENT_STATUS_PRICING_UNAVAILABLE
        return MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
    return MEASUREMENT_STATUS_NO_OBSERVED_ACTIVITY


def _metric_warning(metric: str, status: str) -> str | None:
    if status == MEASUREMENT_STATUS_NO_OBSERVED_ACTIVITY:
        return "Not measurable: no observed activity in the window for this metric."
    if status == MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE:
        return (
            "Not measurable: the observed window does not meet the minimum "
            "autonomous execution sample required for value measurement."
        )
    if status == MEASUREMENT_STATUS_PRICING_UNAVAILABLE:
        return (
            "Not measurable: LLM pricing is not configured, so cost-based value "
            "and ROI cannot be measured."
        )
    return None


def compare_outcomes(
    *,
    baseline: dict,
    targets: dict[str, Any],
    observed: dict,
    projected: dict | None = None,
    source_window_days: int | None = None,
    source_evaluated: bool | None = None,
    planned_measurement_days: int,
) -> dict:
    """Compute the deterministic outcome comparison for a completed experiment.

    ``baseline`` is the persisted baseline_snapshot, ``observed`` the measured
    observed_outcome snapshot, and ``targets`` the operator-set target metrics.
    ``projected`` is a Phase 1N scenario's persisted ``projected`` dict (never
    the projected-baseline, and only when the source scenario was evaluated).

    Deterministic: identical inputs produce byte-identical output. Neither the
    inputs nor any module state is mutated.
    """
    target_values = _validated_targets(targets)
    if planned_measurement_days <= 0:
        raise ValueError("planned_measurement_days must be positive")

    value_block = observed.get("value") if isinstance(observed, dict) else None
    value_measurement_status = (
        value_block.get("measurement_status") if isinstance(value_block, dict) else None
    )
    value_measurement_status = (
        str(value_measurement_status) if value_measurement_status else None
    )

    incomplete_window = _window_incomplete(observed, planned_measurement_days)
    observed_activity = _observed_activity(observed)

    warnings: list[str] = []
    limitations: list[str] = list(_ALWAYS_LIMITATIONS)

    source_evaluated_bool = bool(source_evaluated)
    projection_available = bool(projected) and source_evaluated_bool
    source_scenario_linked = source_window_days is not None

    if source_evaluated and not projected:
        limitations.append(
            "The linked source scenario is evaluated but carries no persisted "
            "projected result, so no projection comparison is available."
        )

    if source_scenario_linked and not source_evaluated_bool:
        limitations.append(
            "The linked source scenario has not been evaluated, so no "
            "projection comparison is available for this experiment."
        )
        warnings.append(
            "No projection comparison: the linked source scenario has not been "
            "evaluated."
        )

    baseline_window_days = baseline.get("window_days")
    if (
        source_window_days is not None
        and baseline_window_days is not None
        and int(source_window_days) != int(baseline_window_days)
    ):
        text = (
            "The linked source scenario was simulated over a "
            f"{int(source_window_days)}-day window, whilst the experiment "
            "baseline was captured over "
            f"{int(baseline_window_days)} days; the projected values "
            "are not directly comparable and no normalization was applied."
        )
        limitations.append(text)
        warnings.append(text)

    if any(metric in VALUE_METRICS for metric in target_values):
        if value_measurement_status == "pricing_unavailable":
            limitations.append(
                "ROI and value metrics are unavailable because LLM pricing is "
                "not configured, so cost-based savings cannot be measured."
            )
            warnings.append(
                "ROI and value metrics were not measurable: LLM pricing is not "
                "configured."
            )
        elif value_measurement_status == "insufficient_sample":
            limitations.append(
                "ROI and value metrics are unavailable because the observed "
                "window does not meet the minimum autonomous execution sample "
                "for value measurement."
            )
            warnings.append(
                "ROI and value metrics were not measurable: the observed window "
                "did not meet the minimum autonomous execution sample."
            )

    if incomplete_window:
        limitations.append(LIMITATION_INCOMPLETE_WINDOW)
        warnings.append("Observed window is shorter than the planned window.")

    metrics: list[dict] = []
    for metric in SUPPORTED_TARGET_METRICS:
        if metric not in target_values:
            continue
        observed_value = _value_at_path(observed, SNAPSHOT_METRIC_PATH[metric])
        baseline_value = _value_at_path(baseline, SNAPSHOT_METRIC_PATH[metric])
        target_value = target_values[metric]

        observed_value = _rounded(float(observed_value)) if observed_value is not None else None
        baseline_value = _rounded(float(baseline_value)) if baseline_value is not None else None

        change_from_baseline = (
            _rounded(observed_value - baseline_value)
            if observed_value is not None and baseline_value is not None
            else None
        )
        variance_from_target = (
            _rounded(observed_value - target_value)
            if observed_value is not None
            else None
        )

        projected_value = None
        variance_from_projection = None
        if projection_available:
            projected_value = _default_projected_value(projected or {}, metric)
            if projected_value is not None and observed_value is not None:
                variance_from_projection = _rounded(
                    observed_value - projected_value
                )
            elif projected_value is None:
                variance_from_projection = None

        status = _metric_value_status(
            metric, observed_value, value_measurement_status
        )

        metrics.append(
            {
                "metric": metric,
                "baseline_value": baseline_value,
                "target_value": round(target_value, _ROUND),
                "observed_value": observed_value,
                "change_from_baseline": change_from_baseline,
                "variance_from_target": variance_from_target,
                "projected_value": projected_value,
                "variance_from_projection": variance_from_projection,
                "unit": _METRIC_SPECS[metric][1],
                "direction_vs_baseline": _direction(change_from_baseline),
                "direction_vs_target": _direction(variance_from_target),
                "direction_vs_projection": _direction(variance_from_projection),
                "measurement_status": status,
                "warning": _metric_warning(metric, status),
            }
        )

    if value_measurement_status == "pricing_unavailable":
        top_status = MEASUREMENT_STATUS_PRICING_UNAVAILABLE
    elif value_measurement_status == "insufficient_sample":
        top_status = MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
    elif incomplete_window:
        top_status = MEASUREMENT_STATUS_INCOMPLETE_WINDOW
    elif not observed_activity:
        top_status = MEASUREMENT_STATUS_NO_OBSERVED_ACTIVITY
    else:
        top_status = MEASUREMENT_STATUS_MEASURED

    return {
        "comparison_version": SERVICE_TRANSFORMATION_EXPERIMENT_VERSION,
        "measurement_status": top_status,
        "window": {
            "start": observed.get("current_window_start"),
            "end": observed.get("current_window_end"),
            "days": observed.get("window_days"),
        },
        "metrics": metrics,
        "warnings": warnings,
        "limitations": limitations,
    }