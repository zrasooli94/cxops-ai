"""Deterministic scenario-modelling engine for Service Transformation.

This is deliberately a pure, side-effect-free module: it takes an observed
baseline snapshot plus operator-supplied 0-100 percentage assumptions and
returns bounded projected values, deltas, and modelling warnings. It contains
no randomness, no current-time dependence, no LLM call, and no database or
system access. Every projected value is labelled scenario arithmetic applied
to the observed baseline — never a forecast or guaranteed business outcome.

Projected rates are reported as 0-100 percentages, mirroring Phase 1L rate
semantics. Value/ROI projections inherit the exact Phase 1L value-realization
semantics (minutes * minutes_per_autonomous_execution, hours * hourly cost,
net / cost ROI) and are only produced when the observed baseline was itself
measurable (pricing configured and minimum autonomous sample met); otherwise
the projection reports an inherited ``measurement_status`` and returns no ROI.

Formulas (one per dimension, all applied independently):

- Autonomous executions:      round(agent_runs * target_rate / 100), clamped
                              to [0, agent_runs]. Rate = the target itself.
- Human approval demand:      round(agent_runs * target_rate / 100), clamped
                              to [0, agent_runs]. Independently projected; it
                              never becomes a partition of agent outcomes.
- Knowledge specialist runs:  round(agent_runs * target_rate / 100).
- Reopened tickets:           round(tickets_resolved * target_rate / 100),
                              using the Phase 1L reopen denominator
                              (reopened_tickets / tickets_resolved).
- SLA breach reduction:       round(total_sla_breaches * (1 - reduction / 100)),
                              clamped at 0.
"""

from __future__ import annotations

from math import isfinite

SERVICE_TRANSFORMATION_SIMULATION_VERSION = "1"

MEASUREMENT_STATUS_PRICING_UNAVAILABLE = "pricing_not_configured"
MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE = "insufficient_sample"
MEASUREMENT_STATUS_MEASURED = "measured"

_ROUND = 2


def _finite(value: float, name: str) -> float:
    if not isfinite(value):
        raise ValueError(f"baseline {name} must be finite")
    return value


def _count(value: int | None, name: str) -> int:
    if value is None or not isinstance(value, int):
        raise ValueError(f"baseline {name} must be an integer count")
    return value


def simulate(baseline: dict, assumptions: dict) -> dict:
    """Project an observed baseline under 0-100 percentage assumptions.

    ``baseline`` must be a normalized snapshot of the Phase 1L summary
    (see ``app.services.service_transformation_simulation_service``), and
    ``assumptions`` a subset of the ``SimulationAssumptions`` keys with
    finite 0-100 values. Deterministic for identical inputs.
    """

    agent_runs = _count(baseline.get("agent_runs"), "agent_runs")
    autonomous_executions = _count(
        baseline.get("autonomous_executions"), "autonomous_executions"
    )
    human_approval_required = _count(
        baseline.get("human_approval_required"), "human_approval_required"
    )
    knowledge_specialist_runs = _count(
        baseline.get("knowledge_specialist_runs"), "knowledge_specialist_runs"
    )
    reopened_tickets = _count(baseline.get("reopened_tickets"), "reopened_tickets")
    tickets_resolved = _count(
        baseline.get("tickets_resolved"), "tickets_resolved"
    )
    total_sla_breaches = _count(
        baseline.get("total_sla_breaches"), "total_sla_breaches"
    )

    for name, value in baseline.get("rates", {}).items():
        if value is not None:
            _finite(value, name)

    projections: dict = {}
    deltas: dict = {}
    warnings: list[str] = []

    _project_autonomous(
        assumptions, agent_runs, autonomous_executions, projections, deltas, warnings
    )
    _project_human_approval(
        assumptions, agent_runs, human_approval_required, projections, deltas, warnings
    )
    _project_knowledge(
        assumptions, agent_runs, knowledge_specialist_runs, projections, deltas, warnings
    )
    _project_reopen(
        assumptions, reopened_tickets, tickets_resolved, projections, deltas, warnings
    )
    _project_sla(
        assumptions, total_sla_breaches, projections, deltas
    )

    if _autonomous_assumed(assumptions) and _human_approval_assumed(assumptions):
        warnings.append(
            "Autonomous execution and human approval projections are each the "
            "target rate applied to the same observed agent runs; they are "
            "independent rates, not a mutual partition, and must not be summed."
        )

    value_status = _project_value(
        baseline, projections["autonomous_executions"], projections, deltas, warnings
    )

    return {
        "formula_version": SERVICE_TRANSFORMATION_SIMULATION_VERSION,
        "projected": projections,
        "deltas": deltas,
        "warnings": warnings,
        "measurement_status": value_status,
    }


def _clamp_count(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _autonomous_assumed(assumptions: dict) -> bool:
    return "autonomous_execution_rate_target" in assumptions


def _human_approval_assumed(assumptions: dict) -> bool:
    return "human_approval_rate_target" in assumptions


def _target(assumptions: dict, key: str) -> float | None:
    value = assumptions.get(key)
    if value is None:
        return None
    _finite(value, key)
    if not 0 <= value <= 100:
        raise ValueError(f"{key} must be a percentage between 0 and 100")
    return value


def _project_autonomous(
    assumptions: dict,
    agent_runs: int,
    autonomous_executions: int,
    projections: dict,
    deltas: dict,
    warnings: list[str],
) -> None:
    target = _target(assumptions, "autonomous_execution_rate_target")
    if target is None:
        projected = autonomous_executions
        rate = None
    else:
        projected = round(agent_runs * target / 100)
        projected = _clamp_count(projected, 0, agent_runs)
        rate = round(float(target), _ROUND)
        if agent_runs == 0:
            warnings.append(
                "The autonomous execution target is applied to a baseline with "
                "zero observed agent runs; the projected count is zero."
            )
        else:
            warnings.append(
                "The autonomous execution projection applies the target rate "
                "to observed agent runs; eligibility is not modelled."
            )

    projections["autonomous_executions"] = projected
    projections["autonomous_execution_rate_percent"] = rate
    deltas["autonomous_executions"] = projected - autonomous_executions


def _project_human_approval(
    assumptions: dict,
    agent_runs: int,
    human_approval_required: int,
    projections: dict,
    deltas: dict,
    warnings: list[str],
) -> None:
    target = _target(assumptions, "human_approval_rate_target")
    if target is None:
        projected = human_approval_required
        rate = None
    else:
        projected = round(agent_runs * target / 100)
        projected = _clamp_count(projected, 0, agent_runs)
        rate = round(float(target), _ROUND)
        if agent_runs == 0:
            warnings.append(
                "The human approval target is applied to a baseline with zero "
                "observed agent runs; the projected count is zero."
            )
        else:
            warnings.append(
                "The human approval projection applies the target rate to "
                "observed agent runs as independent demand; it is not a "
                "forecast of approval volume after automation."
            )

    projections["human_approval_required"] = projected
    projections["human_approval_rate_percent"] = rate
    deltas["human_approval_required"] = projected - human_approval_required


def _project_knowledge(
    assumptions: dict,
    agent_runs: int,
    knowledge_specialist_runs: int,
    projections: dict,
    deltas: dict,
    warnings: list[str],
) -> None:
    target = _target(assumptions, "knowledge_usage_rate_target")
    if target is None:
        projected = knowledge_specialist_runs
        rate = None
    else:
        projected = round(agent_runs * target / 100)
        projected = _clamp_count(projected, 0, agent_runs)
        rate = round(float(target), _ROUND)
        if agent_runs == 0:
            warnings.append(
                "The knowledge usage target is applied to a baseline with zero "
                "observed agent runs; the projected count is zero."
            )
        else:
            warnings.append(
                "The knowledge usage projection applies the target rate to "
                "observed agent runs; it projects usage, not resolution quality."
            )

    projections["knowledge_specialist_runs"] = projected
    projections["knowledge_usage_rate_percent"] = rate
    deltas["knowledge_specialist_runs"] = projected - knowledge_specialist_runs


def _project_reopen(
    assumptions: dict,
    reopened_tickets: int,
    tickets_resolved: int,
    projections: dict,
    deltas: dict,
    warnings: list[str],
) -> None:
    target = _target(assumptions, "reopen_rate_target")
    if target is None:
        projected = reopened_tickets
        rate = None
    else:
        projected = round(tickets_resolved * target / 100)
        projected = _clamp_count(projected, 0, tickets_resolved)
        rate = round(float(target), _ROUND)
        if tickets_resolved == 0:
            warnings.append(
                "The reopen target is applied to a baseline with zero resolved "
                "tickets (the Phase 1L reopen denominator); the projected count "
                "is zero."
            )
        else:
            warnings.append(
                "The reopen projection applies the target rate to observed "
                "resolved tickets using the same denominator as the Phase 1L "
                "reopen rate; it is scenario arithmetic, not a forecast."
            )

    projections["reopened_tickets"] = projected
    projections["reopen_rate_percent"] = rate
    deltas["reopened_tickets"] = projected - reopened_tickets


def _project_sla(
    assumptions: dict,
    total_sla_breaches: int,
    projections: dict,
    deltas: dict,
) -> None:
    target = _target(assumptions, "sla_breach_reduction_percent")
    if target is None:
        projected = total_sla_breaches
        reduction = None
    else:
        projected = round(total_sla_breaches * (1 - target / 100))
        projected = max(0, projected)
        reduction = round(float(target), _ROUND)

    projections["total_sla_breaches"] = projected
    projections["sla_breach_reduction_percent"] = reduction
    deltas["total_sla_breaches"] = projected - total_sla_breaches


def _project_value(
    baseline: dict,
    projected_autonomous: int,
    projections: dict,
    deltas: dict,
    warnings: list[str],
) -> str:
    """Project value using Phase 1L mechanics or report why it is unavailable.

    The projected value is the observed instrumented value scaled by the
    projected/observed autonomous-execution ratio. Projected AI cost is held at
    the observed cost because no assumption changes the total number of runs
    the instrumented agent executes. ROI is only (re)computed — using the Phase
    1L net/cost formulation — when the observed baseline was itself measurable;
    otherwise ``measurement_status`` is inherited unchanged and ROI is None.
    """
    value = baseline.get("value") or {}
    value_status = str(value.get("measurement_status", "measured"))

    observed = {
        "estimated_minutes_saved": _finite(
            float(value["estimated_minutes_saved"]), "value.estimated_minutes_saved"
        ),
        "estimated_hours_saved": _finite(
            float(value["estimated_hours_saved"]), "value.estimated_hours_saved"
        ),
        "estimated_labor_savings_usd": _finite(
            float(value["estimated_labor_savings_usd"]),
            "value.estimated_labor_savings_usd",
        ),
        "agent_ai_cost_usd": _finite(
            float(value["agent_ai_cost_usd"]), "value.agent_ai_cost_usd"
        ),
        "estimated_net_savings_usd": _finite(
            float(value["estimated_net_savings_usd"]),
            "value.estimated_net_savings_usd",
        ),
    }

    projected_value: dict = {
        "estimated_minutes_saved": round(observed["estimated_minutes_saved"], _ROUND),
        "estimated_hours_saved": round(observed["estimated_hours_saved"], _ROUND),
        "estimated_labor_savings_usd": round(
            observed["estimated_labor_savings_usd"], _ROUND
        ),
        "agent_ai_cost_usd": round(observed["agent_ai_cost_usd"], 6),
        "estimated_net_savings_usd": round(observed["estimated_net_savings_usd"], 6),
        "roi_percent": None,
    }

    pricing_configured = bool(value.get("pricing_configured", False))
    sample_size_sufficient = bool(value.get("sample_size_sufficient", False))
    minimum_autonomous_samples = value.get("minimum_autonomous_samples")
    observed_autonomous = _count(
        baseline.get("autonomous_executions"), "autonomous_executions"
    )

    if not pricing_configured:
        value_status = MEASUREMENT_STATUS_PRICING_UNAVAILABLE
        warnings.append(
            "Projected ROI is unavailable because LLM pricing is not "
            "configured; the observed value baseline carries the same status."
        )
    elif not sample_size_sufficient:
        value_status = MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
        warnings.append(
            "Projected ROI is unavailable because the observed baseline does "
            "not meet the minimum autonomous execution sample"
            + (
                f" ({minimum_autonomous_samples})"
                if isinstance(minimum_autonomous_samples, int)
                else ""
            )
            + "; the observed value baseline carries the same status."
        )
    else:
        if observed_autonomous > 0:
            scaling = max(projected_autonomous, 0) / observed_autonomous
            projected_value["estimated_minutes_saved"] = round(
                observed["estimated_minutes_saved"] * scaling, _ROUND
            )
            projected_value["estimated_hours_saved"] = round(
                observed["estimated_hours_saved"] * scaling, _ROUND
            )
            projected_value["estimated_labor_savings_usd"] = round(
                observed["estimated_labor_savings_usd"] * scaling, _ROUND
            )
            projected_value["estimated_net_savings_usd"] = round(
                projected_value["estimated_labor_savings_usd"]
                - projected_value["agent_ai_cost_usd"],
                6,
            )
            if projected_value["agent_ai_cost_usd"] > 0:
                projected_value["roi_percent"] = round(
                    (projected_value["estimated_net_savings_usd"]
                     / projected_value["agent_ai_cost_usd"]) * 100,
                    _ROUND,
                )
            value_status = MEASUREMENT_STATUS_MEASURED
            warnings.append(
                "The value projection scales observed instrumented value by "
                "the projected/observed autonomous-execution ratio; projected "
                "AI cost is held at the observed baseline because no assumption "
                "changes total agent runs."
            )
        else:
            value_status = MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE
            warnings.append(
                "Projected ROI is unavailable because the observed baseline "
                "recorded zero instrumented autonomous executions."
            )

    for key in (
        "estimated_minutes_saved",
        "estimated_hours_saved",
        "estimated_labor_savings_usd",
        "estimated_net_savings_usd",
    ):
        deltas[key] = round(
            float(projected_value[key]) - float(observed[key]), _ROUND
        )

    projections["value"] = projected_value
    projections["value"]["measurement_status"] = value_status
    return value_status