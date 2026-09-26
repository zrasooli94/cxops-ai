"""Measurement snapshots for Service Transformation experiments.

Every snapshot is computed server-side from live tenant telemetry via the same
Phase 1L repository aggregates the transformation analytics use — never from a
client payload and never from the simulation engine. The baseline snapshot and
the observed-outcome snapshot share one builder, so their shapes are
identical and the comparison engine can diff them metric-for-metric.

SLA breach outcomes reuse the canonical Phase 1L persisted
``ServiceEscalation`` milestone semantics (rows triggered in the window whose
current stage is ``breached``, split by ``first_response`` / ``resolution``),
so observed outcomes are comparable with Phase 1N scenario projections; they
are never derived from ticket-deadline analytics. Response-duration analytics
(``average_first_response_minutes`` / ``average_resolution_time_minutes``)
remain ticket-based performance metrics and are unrelated to the breach count.

Value/ROI metrics are organization-scoped only: for queue scope the ``value``
block is ``None`` and value metrics are rejected at the API schema layer,
because autonomous-execution economics are not attributable to a single queue.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.service_escalation_repository import (
    ServiceEscalationRepository,
)
from app.repositories.service_transformation_repository import (
    ServiceTransformationRepository,
)
from app.services.agent_observability_service import AgentObservabilityService
from app.services.agent_workflow_service import derive_specialist_path

SCOPE_TYPE_ORGANIZATION = "organization"
SCOPE_TYPE_QUEUE = "queue"

_ROUND = 2

# AgentObservabilityService.roi_summary status vocabulary -> engine vocabulary.
_VALUE_STATUS_MAP = {
    "pricing_not_configured": "pricing_unavailable",
    "insufficient_sample": "insufficient_sample",
    "measured": "measured",
}


def _pct(value: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round((value / total) * 100, _ROUND)


def _knowledge_runs(paths: list[list[str]]) -> int:
    return sum(1 for path in paths if "knowledge" in derive_specialist_path(path))


async def metrics_snapshot_for_window(
    db: AsyncSession,
    *,
    organization_id: int,
    scope_type: str,
    scope_key: str | None,
    queue_id: int | None,
    start: datetime,
    end: datetime,
    window_days: float,
    observed_at: datetime | None = None,
) -> dict:
    """Build a normalized Phase 1L-compatible snapshot over ``[start, end]``.

    ``window_days`` is the nominal window for a baseline capture and the
    elapsed (possibly partial) window for an observed measurement; both are
    persisted so the comparison can detect a short observed window.
    """
    if observed_at is None:
        observed_at = datetime.now(timezone.utc)

    fr = await ServiceTransformationRepository.first_response_analytics_for_window(
        db,
        organization_id=organization_id,
        start=start,
        end=end,
        queue_id=queue_id,
    )
    res = await ServiceTransformationRepository.resolution_analytics_for_window(
        db,
        organization_id=organization_id,
        start=start,
        end=end,
        queue_id=queue_id,
    )
    reopened = await ServiceTransformationRepository.reopened_in_window(
        db,
        organization_id=organization_id,
        start=start,
        end=end,
        queue_id=queue_id,
    )
    runs = await ServiceTransformationRepository.agent_run_counts_for_window(
        db,
        organization_id=organization_id,
        start=start,
        end=end,
        queue_id=queue_id,
    )
    paths = (
        await ServiceTransformationRepository.specialist_workflow_paths_for_window(
            db,
            organization_id=organization_id,
            start=start,
            end=end,
            queue_id=queue_id,
        )
    )

    value: dict | None = None
    if scope_type == SCOPE_TYPE_ORGANIZATION:
        roi = await AgentObservabilityService.roi_summary(
            db,
            organization_id,
            start=start,
            end=end,
        )
        value = {
            "estimated_minutes_saved": roi["estimated_minutes_saved"],
            "estimated_hours_saved": roi["estimated_hours_saved"],
            "estimated_labor_savings_usd": roi["estimated_labor_savings_usd"],
            "agent_ai_cost_usd": roi["agent_ai_cost_usd"],
            "estimated_net_savings_usd": roi["estimated_net_savings_usd"],
            "pricing_configured": bool(roi["pricing_configured"]),
            "measurement_status": _VALUE_STATUS_MAP.get(
                str(roi.get("measurement_status", "measured")), "insufficient_sample"
            ),
            "minimum_autonomous_samples": roi["minimum_autonomous_samples"],
            "sample_size_sufficient": bool(roi["sample_size_sufficient"]),
            "roi_percent": roi["roi_percent"],
        }

    knowledge_runs = _knowledge_runs(paths)
    run_total = runs["runs"]

    sla = await ServiceEscalationRepository.breached_counts_for_window(
        db,
        organization_id=organization_id,
        start=start,
        end=end,
        queue_id=queue_id,
    )

    snapshot: dict = {
        "scope_type": scope_type,
        "scope_key": scope_key,
        "window_days": window_days,
        "observed_at": observed_at.isoformat(),
        "current_window_start": start.isoformat(),
        "current_window_end": end.isoformat(),
        "tickets_resolved": res["count"],
        "reopened_tickets": reopened["distinct_tickets"],
        "first_responses": fr["count"],
        "reopen_events": reopened["events"],
        "first_response_sla_breaches": sla["first_response_sla_breaches"],
        "resolution_sla_breaches": sla["resolution_sla_breaches"],
        "total_sla_breaches": sla["total_sla_breaches"],
        "agent_runs": run_total,
        "autonomous_executions": runs["autonomous"],
        "human_approval_required": runs["approval_required"],
        "knowledge_specialist_runs": knowledge_runs,
        "average_first_response_minutes": fr["avg_minutes"],
        "average_resolution_time_minutes": res["avg_minutes"],
        "rates": {
            "autonomous_execution_rate": _pct(runs["autonomous"], run_total),
            "human_approval_rate": _pct(runs["approval_required"], run_total),
            "knowledge_usage_rate": _pct(knowledge_runs, run_total),
            "reopen_rate": _pct(reopened["distinct_tickets"], res["count"]),
        },
        "value": value,
    }
    return snapshot