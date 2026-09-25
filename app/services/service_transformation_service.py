from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.service_escalation_repository import (
    ServiceEscalationRepository,
)
from app.repositories.service_transformation_repository import (
    ServiceTransformationRepository,
)
from app.schemas.service_transformation import (
    ServiceTransformationAIAdoption,
    ServiceTransformationChannelBreakdownItem,
    ServiceTransformationComparison,
    ServiceTransformationHumanWorkload,
    ServiceTransformationOpportunitySignal,
    ServiceTransformationPerformance,
    ServiceTransformationQueueBreakdownItem,
    ServiceTransformationSLA,
    ServiceTransformationSpecialistUsage,
    ServiceTransformationSummary,
    ServiceTransformationValueRealization,
    ServiceTransformationVolume,
    ServiceTransformationWindow,
)
from app.services.agent_observability_service import AgentObservabilityService
from app.services.agent_workflow_service import (
    derive_specialist_path,
    validate_specialist_path,
)

VALID_TRANSFORMATION_DAYS = (7, 30, 90)
DEFAULT_TRANSFORMATION_DAYS = 30

# --- Deterministic opportunity-signal thresholds ---------------------------
# Conservative, explicit, and pinned by tests. A signal is emitted only when
# the windowed data crosses these floors; the evidence dict carries the
# numbers that triggered it, so the caller can judge the recommendation.
# Signals are queue-scoped where a queue is the actionable unit, otherwise
# organization-scoped.
MIN_QUEUE_AT_RISK_FOR_SLA_PRESSURE = 10
MIN_AGENT_RUNS_FOR_AI_ADOPTION = 8
AUTONOMOUS_RATE_FLOOR_FOR_AI_ADOPTION = 0.20
MIN_AGENT_RUNS_FOR_KNOWLEDGE = 6
KNOWLEDGE_USAGE_FLOOR = 0.50
MIN_APPROVALS_FOR_BACKLOG = 5
MIN_REOPENS_FOR_REOPEN_RISK = 5
REOPEN_RATE_FLOOR = 0.15

SIGNAL_SLA_PRESSURE = "sla_pressure"
SIGNAL_AI_ADOPTION = "ai_adoption"
SIGNAL_KNOWLEDGE_UTILIZATION = "knowledge_utilization"
SIGNAL_APPROVAL_BACKLOG = "approval_backlog"
SIGNAL_REOPEN_RISK = "reopen_risk"


class ServiceTransformationService:
    """Production-style service transformation analytics for one tenant.

    Everything is computed over bounded windows ([now-2d, now-d] for the
    previous window, [now-d, now] for the current), every aggregate is bound to
    ``organization_id`` in SQL, and every window metric is compared
    current-vs-previous with a bounded label-free comparison. No employee-level
    scoring, no fabrications, no content parsing.
    """

    ALLOWED_DAYS = VALID_TRANSFORMATION_DAYS

    @classmethod
    async def summary_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        days: int = DEFAULT_TRANSFORMATION_DAYS,
        now: datetime | None = None,
    ) -> ServiceTransformationSummary:
        if days not in cls.ALLOWED_DAYS:
            raise ValueError(f"days must be one of {cls.ALLOWED_DAYS}")

        if now is None:
            now = datetime.now(UTC)

        current_start = now - timedelta(days=days)
        previous_start = current_start - timedelta(days=days)

        current = await cls._window_snapshot(
            db,
            organization_id=organization_id,
            start=current_start,
            end=now,
            escalation_now=now,
            days=days,
        )
        previous = await cls._window_snapshot(
            db,
            organization_id=organization_id,
            start=previous_start,
            end=current_start,
            escalation_now=current_start,
            days=days,
        )

        current_volume = cls._build_volume(current)
        current_performance = cls._build_performance(current)
        current_sla = cls._build_sla(current)
        current_ai = cls._build_ai_adoption(current)
        current_specialist = cls._build_specialist_usage(current)
        current_workload = cls._build_human_workload(current)
        current_value = cls._build_value_realization(current)

        previous_volume = cls._build_volume(previous)
        previous_performance = cls._build_performance(previous)
        previous_sla = cls._build_sla(previous)
        previous_ai = cls._build_ai_adoption(previous)
        previous_specialist = cls._build_specialist_usage(previous)
        previous_workload = cls._build_human_workload(previous)
        previous_value = cls._build_value_realization(previous)

        comparisons = cls._build_comparisons(
            current_sections=[
                current_volume,
                current_performance,
                current_sla,
                current_ai,
                current_specialist,
                current_workload,
                current_value,
            ],
            previous_sections=[
                previous_volume,
                previous_performance,
                previous_sla,
                previous_ai,
                previous_specialist,
                previous_workload,
                previous_value,
            ],
        )

        queue_breakdown = await cls._queue_breakdown(
            db,
            organization_id=organization_id,
            current_start=current_start,
            end=now,
        )

        channel_breakdown = await cls._channel_breakdown(
            db,
            organization_id=organization_id,
            start=current_start,
            end=now,
        )

        opportunity_signals = cls._opportunity_signals(
            queue_breakdown=queue_breakdown,
            current_ai=current_ai,
            current_specialist=current_specialist,
            current_sla=current_sla,
        )

        snapshot = await ServiceTransformationRepository.current_snapshot_for_tenant(
            db,
            organization_id=organization_id,
        )

        current_volume = ServiceTransformationVolume(
            tickets_created=current["created"],
            tickets_resolved=current["resolved_count"],
            currently_open=snapshot["open"],
            currently_needs_response=snapshot["needs_response"],
        )

        return ServiceTransformationSummary(
            generated_at=now,
            window=ServiceTransformationWindow(
                days=days,
                current_start=current_start,
                current_end=now,
                previous_start=previous_start,
                previous_end=current_start,
            ),
            service_volume=current_volume,
            service_performance=current_performance,
            sla=current_sla,
            ai_adoption=current_ai,
            specialist_usage=current_specialist,
            human_workload=current_workload,
            value_realization=current_value,
            queue_breakdown=queue_breakdown,
            channel_breakdown=channel_breakdown,
            comparisons=comparisons,
            opportunity_signals=opportunity_signals,
        )

    @classmethod
    async def _window_snapshot(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
        escalation_now: datetime,
        days: int,
    ) -> dict:
        created = await ServiceTransformationRepository.tickets_created_in_window(
            db,
            organization_id=organization_id,
            start=start,
            end=end,
        )
        fr = await ServiceTransformationRepository.first_response_analytics_for_window(
            db,
            organization_id=organization_id,
            start=start,
            end=end,
        )
        res = await ServiceTransformationRepository.resolution_analytics_for_window(
            db,
            organization_id=organization_id,
            start=start,
            end=end,
        )
        reopened = await ServiceTransformationRepository.reopened_in_window(
            db,
            organization_id=organization_id,
            start=start,
            end=end,
        )
        runs = await ServiceTransformationRepository.agent_run_counts_for_window(
            db,
            organization_id=organization_id,
            start=start,
            end=end,
        )
        paths = (
            await ServiceTransformationRepository.specialist_workflow_paths_for_window(
                db,
                organization_id=organization_id,
                start=start,
                end=end,
            )
        )
        workload = (
            await ServiceTransformationRepository.human_workload_counts_for_window(
                db,
                organization_id=organization_id,
                start=start,
                end=end,
            )
        )
        escalation = await ServiceEscalationRepository.windowed_summary_for_tenant(
            db,
            organization_id=organization_id,
            now=escalation_now,
            days=days,
        )
        roi = await AgentObservabilityService.roi_summary(
            db,
            organization_id,
            start=start,
            end=end,
        )

        by_milestone = {b.milestone: b for b in escalation.by_milestone}
        fr_bucket = by_milestone.get("first_response")
        res_bucket = by_milestone.get("resolution")

        specialist = cls._specialist_profile(paths)

        return {
            "created": created,
            "resolved_count": res["count"],
            "fr_count": fr["count"],
            "fr_avg_minutes": fr["avg_minutes"],
            "fr_median_minutes": fr["median_minutes"],
            "fr_breached": fr["breached"],
            "res_avg_minutes": res["avg_minutes"],
            "res_median_minutes": res["median_minutes"],
            "res_breached": res["breached"],
            "escalation_count": escalation.triggered_in_window,
            "due_soon": escalation.due_soon_in_window,
            "fr_sla_breaches": (
                fr_bucket.breached_in_window if fr_bucket else 0
            ),
            "res_sla_breaches": (
                res_bucket.breached_in_window if res_bucket else 0
            ),
            "reopened_tickets": reopened["distinct_tickets"],
            "reopened_events": reopened["events"],
            "runs": runs["runs"],
            "distinct_tickets_analyzed": runs["distinct_tickets"],
            "approval_required": runs["approval_required"],
            "autonomous": runs["autonomous"],
            "human_approved": runs["human_approved"],
            "human_rejected": runs["human_rejected"],
            "executed": runs["executed"],
            "execution_failed": runs["execution_failed"],
            "no_action": runs["no_action"],
            "coordinator_runs": specialist["coordinator_runs"],
            "knowledge_runs": specialist["knowledge_runs"],
            "action_runs": specialist["action_runs"],
            "pure_action_runs": specialist["pure_action_runs"],
            "invalid_specialist_paths": specialist["invalid_specialist_paths"],
            "human_messages_sent": workload["human_sent"],
            "ai_executed_replies": workload["ai_executed"],
            "roi": roi,
        }

    @staticmethod
    def _specialist_profile(paths: list[list[str]]) -> dict:
        coordinator = 0
        knowledge = 0
        action = 0
        pure_action = 0
        invalid = 0

        for path in paths:
            derived = derive_specialist_path(path)
            coordinator += "coordinator" in derived
            knowledge += "knowledge" in derived
            action += "action" in derived
            pure_action += ("action" in derived) and ("knowledge" not in derived)

            valid, _ = validate_specialist_path(path)
            if not valid:
                invalid += 1

        return {
            "coordinator_runs": coordinator,
            "knowledge_runs": knowledge,
            "action_runs": action,
            "pure_action_runs": pure_action,
            "invalid_specialist_paths": invalid,
        }

    @staticmethod
    def _pct(value: int, total: int) -> float | None:
        if total <= 0:
            return None
        return round((value / total) * 100, 2)

    @staticmethod
    def _build_volume(data: dict) -> ServiceTransformationVolume:
        return ServiceTransformationVolume(
            tickets_created=data["created"],
            tickets_resolved=data["resolved_count"],
            currently_open=0,
            currently_needs_response=0,
        )

    @staticmethod
    def _build_performance(data: dict) -> ServiceTransformationPerformance:
        return ServiceTransformationPerformance(
            average_first_response_minutes=data["fr_avg_minutes"],
            median_first_response_minutes=data["fr_median_minutes"],
            average_resolution_time_minutes=data["res_avg_minutes"],
            median_resolution_time_minutes=data["res_median_minutes"],
        )

    @staticmethod
    def _build_sla(data: dict) -> ServiceTransformationSLA:
        total_breaches = data["fr_sla_breaches"] + data["res_sla_breaches"]
        return ServiceTransformationSLA(
            first_response_sla_breaches=data["fr_sla_breaches"],
            resolution_sla_breaches=data["res_sla_breaches"],
            total_sla_breaches=total_breaches,
            due_soon=data["due_soon"],
            escalation_count=data["escalation_count"],
            escalation_rate=round(
                (data["escalation_count"] / data["created"]) * 100, 2
            )
            if data["created"] > 0
            else None,
            reopened_tickets=data["reopened_tickets"],
            reopen_rate=round(
                (data["reopened_tickets"] / data["resolved_count"]) * 100, 2
            )
            if data["resolved_count"] > 0
            else None,
        )

    @staticmethod
    def _build_ai_adoption(data: dict) -> ServiceTransformationAIAdoption:
        runs = data["runs"]
        attempted = data["executed"] + data["execution_failed"]
        return ServiceTransformationAIAdoption(
            tickets_analyzed_by_ai=data["distinct_tickets_analyzed"],
            agent_runs=runs,
            autonomous_executions=data["autonomous"],
            human_approval_required=data["approval_required"],
            human_approved=data["human_approved"],
            human_rejected=data["human_rejected"],
            successful_agent_executions=data["executed"],
            failed_agent_executions=data["execution_failed"],
            no_action_runs=data["no_action"],
            ai_analysis_rate=(
                round((data["distinct_tickets_analyzed"] / data["created"]) * 100, 2)
                if data["created"] > 0
                else None
            ),
            autonomous_execution_rate=(
                round((data["autonomous"] / runs) * 100, 2) if runs > 0 else None
            ),
            human_approval_rate=(
                round((data["approval_required"] / runs) * 100, 2)
                if runs > 0
                else None
            ),
            execution_success_rate=(
                round((data["executed"] / attempted) * 100, 2)
                if attempted > 0
                else None
            ),
        )

    @staticmethod
    def _build_specialist_usage(data: dict) -> ServiceTransformationSpecialistUsage:
        runs = data["runs"]
        return ServiceTransformationSpecialistUsage(
            coordinator_runs=data["coordinator_runs"],
            knowledge_specialist_runs=data["knowledge_runs"],
            action_specialist_runs=data["action_runs"],
            knowledge_usage_rate=(
                round((data["knowledge_runs"] / runs) * 100, 2) if runs > 0 else None
            ),
            pure_action_route_rate=(
                round((data["pure_action_runs"] / runs) * 100, 2)
                if runs > 0
                else None
            ),
            invalid_specialist_path_count=data["invalid_specialist_paths"],
        )

    @staticmethod
    def _build_human_workload(data: dict) -> ServiceTransformationHumanWorkload:
        return ServiceTransformationHumanWorkload(
            human_messages_sent=data["human_messages_sent"],
            ai_executed_replies=data["ai_executed_replies"],
        )

    @staticmethod
    def _build_value_realization(data: dict) -> ServiceTransformationValueRealization:
        roi = data["roi"]
        return ServiceTransformationValueRealization(
            estimated_minutes_saved=roi["estimated_minutes_saved"],
            estimated_hours_saved=roi["estimated_hours_saved"],
            estimated_labor_savings_usd=roi["estimated_labor_savings_usd"],
            agent_ai_cost_usd=roi["agent_ai_cost_usd"],
            estimated_net_savings_usd=roi["estimated_net_savings_usd"],
            pricing_configured=roi["pricing_configured"],
            measurement_status=roi["measurement_status"],
            minimum_autonomous_samples=roi["minimum_autonomous_samples"],
            sample_size_sufficient=roi["sample_size_sufficient"],
            roi_percent=roi["roi_percent"],
        )

    @staticmethod
    def _flat_metrics(sections: list) -> dict[str, int | float | None]:
        metrics: dict[str, int | float | None] = {}

        volume, performance, sla, ai, specialist, workload, value = sections

        metrics.update(
            {
                "tickets_created": volume.tickets_created,
                "tickets_resolved": volume.tickets_resolved,
                "average_first_response_minutes": (
                    performance.average_first_response_minutes
                ),
                "median_first_response_minutes": (
                    performance.median_first_response_minutes
                ),
                "average_resolution_time_minutes": (
                    performance.average_resolution_time_minutes
                ),
                "median_resolution_time_minutes": (
                    performance.median_resolution_time_minutes
                ),
                "first_response_sla_breaches": sla.first_response_sla_breaches,
                "resolution_sla_breaches": sla.resolution_sla_breaches,
                "total_sla_breaches": sla.total_sla_breaches,
                "due_soon": sla.due_soon,
                "escalation_count": sla.escalation_count,
                "escalation_rate": sla.escalation_rate,
                "reopened_tickets": sla.reopened_tickets,
                "reopen_rate": sla.reopen_rate,
            }
        )

        metrics.update(
            {
                "tickets_analyzed_by_ai": ai.tickets_analyzed_by_ai,
                "agent_runs": ai.agent_runs,
                "autonomous_executions": ai.autonomous_executions,
                "human_approval_required": ai.human_approval_required,
                "human_approved": ai.human_approved,
                "human_rejected": ai.human_rejected,
                "successful_agent_executions": ai.successful_agent_executions,
                "failed_agent_executions": ai.failed_agent_executions,
                "no_action_runs": ai.no_action_runs,
                "ai_analysis_rate": ai.ai_analysis_rate,
                "autonomous_execution_rate": ai.autonomous_execution_rate,
                "human_approval_rate": ai.human_approval_rate,
                "execution_success_rate": ai.execution_success_rate,
            }
        )

        metrics.update(
            {
                "coordinator_runs": specialist.coordinator_runs,
                "knowledge_specialist_runs": specialist.knowledge_specialist_runs,
                "action_specialist_runs": specialist.action_specialist_runs,
                "knowledge_usage_rate": specialist.knowledge_usage_rate,
                "pure_action_route_rate": specialist.pure_action_route_rate,
                "invalid_specialist_path_count": specialist.invalid_specialist_path_count,
            }
        )

        metrics.update(
            {
                "human_messages_sent": workload.human_messages_sent,
                "ai_executed_replies": workload.ai_executed_replies,
            }
        )

        metrics.update(
            {
                "estimated_minutes_saved": value.estimated_minutes_saved,
                "estimated_hours_saved": value.estimated_hours_saved,
                "estimated_labor_savings_usd": value.estimated_labor_savings_usd,
                "agent_ai_cost_usd": value.agent_ai_cost_usd,
                "estimated_net_savings_usd": value.estimated_net_savings_usd,
                "roi_percent": value.roi_percent,
            }
        )

        return metrics

    @staticmethod
    def _build_comparisons(
        *,
        current_sections: list,
        previous_sections: list,
    ) -> dict[str, ServiceTransformationComparison]:
        current = ServiceTransformationService._flat_metrics(current_sections)
        previous = ServiceTransformationService._flat_metrics(previous_sections)

        comparisons: dict[str, ServiceTransformationComparison] = {}
        for key in current:
            if key not in previous:
                continue
            c, p = current[key], previous[key]
            absolute_change: int | float | None = None
            percent_change: float | None = None

            if c is not None and p is not None:
                absolute_change = round(c - p, 2)
                if p != 0:
                    percent_change = round(((c - p) / p) * 100, 2)

            comparisons[key] = ServiceTransformationComparison(
                current=c,
                previous=p,
                absolute_change=absolute_change,
                percent_change=percent_change,
            )

        return comparisons

    @classmethod
    async def _queue_breakdown(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        current_start: datetime,
        end: datetime,
    ) -> list[ServiceTransformationQueueBreakdownItem]:
        rows = await ServiceTransformationRepository.queue_breakdown_for_tenant(
            db,
            organization_id=organization_id,
        )
        escalation_by_queue = (
            await ServiceTransformationRepository.escalation_current_by_queue(
                db,
                organization_id=organization_id,
            )
        )
        resolved_by_queue = (
            await ServiceTransformationRepository.resolved_per_queue_in_window(
                db,
                organization_id=organization_id,
                start=current_start,
                end=end,
            )
        )
        runs_by_queue = await ServiceTransformationRepository.agent_runs_per_queue_in_window(
            db,
            organization_id=organization_id,
            start=current_start,
            end=end,
        )

        items: list[ServiceTransformationQueueBreakdownItem] = []
        for row in rows:
            queue_id = row["queue_id"]
            escal = escalation_by_queue.get(queue_id, {"due_soon": 0, "breached": 0})
            runs = runs_by_queue.get(queue_id, {"runs": 0, "autonomous": 0})
            items.append(
                ServiceTransformationQueueBreakdownItem(
                    queue_key=row["queue_key"],
                    queue_name=row["queue_name"],
                    open_tickets=row["open_tickets"],
                    needs_response=row["needs_response"],
                    due_soon=escal["due_soon"],
                    breached=escal["breached"],
                    priority_urgent_high=row["priority_urgent_high"],
                    assigned_tickets=row["assigned_tickets"],
                    resolved_in_window=resolved_by_queue.get(queue_id, 0),
                    agent_runs=runs["runs"],
                    autonomous_executions=runs["autonomous"],
                )
            )
        return items

    @classmethod
    async def _channel_breakdown(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
    ) -> list[ServiceTransformationChannelBreakdownItem]:
        conversations = (
            await ServiceTransformationRepository.channel_conversation_counts_for_window(
                db,
                organization_id=organization_id,
                start=start,
                end=end,
            )
        )
        messages = (
            await ServiceTransformationRepository.channel_message_counts_for_window(
                db,
                organization_id=organization_id,
                start=start,
                end=end,
            )
        )

        total_conversations = sum(conversations.values())
        items: list[ServiceTransformationChannelBreakdownItem] = []
        for channel in sorted(conversations):
            conv_count = conversations[channel]
            msg_count = messages.get(channel, 0)
            items.append(
                ServiceTransformationChannelBreakdownItem(
                    channel=channel,
                    conversation_count=conv_count,
                    message_count=msg_count,
                    percentage=round(conv_count / total_conversations * 100, 2)
                    if total_conversations > 0
                    else 0.0,
                )
            )
        return items

    @staticmethod
    def _opportunity_signals(
        *,
        queue_breakdown: list[ServiceTransformationQueueBreakdownItem],
        current_ai: ServiceTransformationAIAdoption,
        current_specialist: ServiceTransformationSpecialistUsage,
        current_sla: ServiceTransformationSLA,
    ) -> list[ServiceTransformationOpportunitySignal]:
        signals: list[ServiceTransformationOpportunitySignal] = []

        for queue in queue_breakdown:
            at_risk = queue.due_soon + queue.breached
            if at_risk >= MIN_QUEUE_AT_RISK_FOR_SLA_PRESSURE:
                signals.append(
                    ServiceTransformationOpportunitySignal(
                        signal=SIGNAL_SLA_PRESSURE,
                        scope_type="queue",
                        scope_key=queue.queue_key or f"queue:{queue.open_tickets}",
                        evidence={
                            "due_soon": queue.due_soon,
                            "breached": queue.breached,
                            "at_risk": at_risk,
                        },
                        suggested_focus=(
                            "Revisit SLA deadlines and prioritization before "
                            "the next breach cycle."
                        ),
                    )
                )

            if queue.agent_runs >= MIN_AGENT_RUNS_FOR_AI_ADOPTION:
                autonomous_rate = (
                    queue.autonomous_executions / queue.agent_runs
                )
                if autonomous_rate < AUTONOMOUS_RATE_FLOOR_FOR_AI_ADOPTION:
                    signals.append(
                        ServiceTransformationOpportunitySignal(
                            signal=SIGNAL_AI_ADOPTION,
                            scope_type="queue",
                            scope_key=queue.queue_key or f"queue:{queue.open_tickets}",
                            evidence={
                                "agent_runs": queue.agent_runs,
                                "autonomous_executions": queue.autonomous_executions,
                                "autonomous_execution_rate": round(
                                    autonomous_rate * 100, 2
                                ),
                            },
                            suggested_focus=(
                                "Expand safe autonomous execution coverage where "
                                "approvals are routine for this queue."
                            ),
                        )
                    )

        if current_ai.agent_runs >= MIN_AGENT_RUNS_FOR_AI_ADOPTION:
            autonomous_rate = (
                current_ai.autonomous_executions / current_ai.agent_runs
            )
            if autonomous_rate < AUTONOMOUS_RATE_FLOOR_FOR_AI_ADOPTION:
                signals.append(
                    ServiceTransformationOpportunitySignal(
                        signal=SIGNAL_AI_ADOPTION,
                        scope_type="organization",
                        scope_key="organization",
                        evidence={
                            "agent_runs": current_ai.agent_runs,
                            "autonomous_executions": current_ai.autonomous_executions,
                            "autonomous_execution_rate": round(
                                autonomous_rate * 100, 2
                            ),
                        },
                        suggested_focus=(
                            "Widen the low-risk auto-approval envelope for "
                            "routinely approved actions."
                        ),
                    )
                )

        if (
            current_ai.agent_runs >= MIN_AGENT_RUNS_FOR_KNOWLEDGE
            and current_specialist.knowledge_usage_rate is not None
            and current_specialist.knowledge_usage_rate < (
                KNOWLEDGE_USAGE_FLOOR * 100
            )
        ):
            signals.append(
                ServiceTransformationOpportunitySignal(
                    signal=SIGNAL_KNOWLEDGE_UTILIZATION,
                    scope_type="organization",
                    scope_key="organization",
                    evidence={
                        "knowledge_usage_rate": current_specialist.knowledge_usage_rate,
                        "agent_runs": current_ai.agent_runs,
                    },
                    suggested_focus=(
                        "Improve retrieval coverage so specialists ground more "
                        "decisions in knowledge."
                    ),
                )
            )

        if current_ai.human_approval_required >= MIN_APPROVALS_FOR_BACKLOG:
            signals.append(
                ServiceTransformationOpportunitySignal(
                    signal=SIGNAL_APPROVAL_BACKLOG,
                    scope_type="organization",
                    scope_key="organization",
                    evidence={
                        "human_approval_required": current_ai.human_approval_required,
                        "agent_runs": current_ai.agent_runs,
                    },
                    suggested_focus=(
                        "Review the approval backlog and widen the low-risk "
                        "auto-approval envelope."
                    ),
                )
            )

        if (
            current_sla.reopen_rate is not None
            and current_sla.reopen_rate >= (REOPEN_RATE_FLOOR * 100)
            and current_sla.reopened_tickets >= MIN_REOPENS_FOR_REOPEN_RISK
        ):
            signals.append(
                ServiceTransformationOpportunitySignal(
                    signal=SIGNAL_REOPEN_RISK,
                    scope_type="organization",
                    scope_key="organization",
                    evidence={
                        "reopened_tickets": current_sla.reopened_tickets,
                        "reopen_rate": current_sla.reopen_rate,
                    },
                    suggested_focus=(
                        "Investigate resolution quality to cut the reopen rate."
                    ),
                )
            )

        signals.sort(key=lambda s: (s.signal, s.scope_key or ""))
        return signals