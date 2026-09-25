"""Service-layer orchestration for Service Transformation scenarios.

Tenancy is always resolved from the caller's ``CurrentTenant``; the service
never accepts an ``organization_id`` from a client payload. Evaluation is the
only write the simulation flow performs outside scenario rows: it captures a
read-only snapshot of the Phase 1L summary, runs the deterministic engine, and
persists baseline + assumptions + result on the scenario row. Operational
tables (Ticket, AgentRun, SLA policies, ServiceEscalation, AIRequestLog) are
never mutated by simulation.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_transformation_scenario import (
    VALID_SCENARIO_STATUSES,
    ServiceTransformationScenario,
)
from app.repositories.service_transformation_scenario_repository import (
    ServiceTransformationScenarioRepository,
)
from app.schemas.service_transformation import ServiceTransformationSummary
from app.schemas.service_transformation_simulation import (
    ServiceTransformationScenarioCreate,
)
from app.services.service_transformation_service import (
    DEFAULT_TRANSFORMATION_DAYS,
    ServiceTransformationService,
)
from app.services.service_transformation_simulation_engine import (
    simulate as engine_simulate,
)


class ScenarioNotFoundError(Exception):
    """The scenario does not exist for the caller's tenant."""


class ScenarioArchivedError(Exception):
    """The scenario is archived and cannot be evaluated."""


class ServiceTransformationSimulationService:
    """Tenant-scoped scenario lifecycle and deterministic evaluation."""

    @staticmethod
    async def create_for_tenant(
        db: AsyncSession,
        *,
        data: ServiceTransformationScenarioCreate,
        organization_id: int,
        created_by_subject: str | None = None,
    ) -> ServiceTransformationScenario:
        payload = data.model_dump()
        assumptions = {
            key: payload["assumptions"][key]
            for key in payload["assumptions"]
            if payload["assumptions"][key] is not None
        }
        if data.days not in (7, 30, 90):
            raise ValueError("days must be one of (7, 30, 90)")
        return await ServiceTransformationScenarioRepository.create_for_tenant(
            db,
            data={
                "name": data.name,
                "description": data.description,
                "days": data.days,
                "assumptions": assumptions,
            },
            organization_id=organization_id,
        )

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> list[ServiceTransformationScenario]:
        return await ServiceTransformationScenarioRepository.list_for_tenant(
            db,
            organization_id=organization_id,
        )

    @staticmethod
    async def get_for_tenant(
        db: AsyncSession,
        *,
        scenario_id: int,
        organization_id: int,
    ) -> ServiceTransformationScenario | None:
        return await ServiceTransformationScenarioRepository.get_by_id_for_tenant(
            db,
            scenario_id=scenario_id,
            organization_id=organization_id,
        )

    @staticmethod
    async def archive_for_tenant(
        db: AsyncSession,
        *,
        scenario_id: int,
        organization_id: int,
    ) -> ServiceTransformationScenario | None:
        scenario = await ServiceTransformationScenarioRepository.get_by_id_for_tenant(
            db,
            scenario_id=scenario_id,
            organization_id=organization_id,
        )
        if scenario is None:
            return None
        if scenario.status == VALID_SCENARIO_STATUSES[2]:
            return scenario
        scenario.status = VALID_SCENARIO_STATUSES[2]
        return await ServiceTransformationScenarioRepository.save(db, scenario)

    @staticmethod
    async def evaluate_for_tenant(
        db: AsyncSession,
        *,
        scenario_id: int,
        organization_id: int,
    ) -> dict:
        """Evaluate a scenario against the tenant's current observed baseline.

        Re-reads the Phase 1L transformation summary via the existing
        ``ServiceTransformationService`` (never a cached or client-supplied
        baseline), runs the deterministic engine, and persists the snapshot so
        the row is self-contained and auditable. Re-evaluating a draft or
        evaluated scenario overwrites the stored snapshot; archived scenarios
        are rejected.
        """
        scenario = await ServiceTransformationScenarioRepository.get_by_id_for_tenant(
            db,
            scenario_id=scenario_id,
            organization_id=organization_id,
        )
        if scenario is None:
            raise ScenarioNotFoundError("Scenario not found")
        if scenario.status == VALID_SCENARIO_STATUSES[2]:
            raise ScenarioArchivedError("Archived scenarios cannot be evaluated")

        days = scenario.window_days if scenario.window_days else DEFAULT_TRANSFORMATION_DAYS
        summary = await ServiceTransformationService.summary_for_tenant(
            db,
            organization_id=organization_id,
            days=days,
        )

        baseline = ServiceTransformationSimulationService._capture_baseline(
            summary=summary,
            organization_id=organization_id,
            days=days,
        )

        result = engine_simulate(
            baseline=baseline,
            assumptions=dict(scenario.assumptions),
        )

        scenario.observed_baseline = baseline
        scenario.projected_result = result["projected"]
        scenario.formula_version = result["formula_version"]
        scenario.evaluated_at = datetime.now(timezone.utc)
        scenario.status = "evaluated"
        await ServiceTransformationScenarioRepository.save(db, scenario)

        return {
            "baseline": baseline,
            "assumptions": dict(scenario.assumptions),
            "projected": result["projected"],
            "deltas": result["deltas"],
            "warnings": result["warnings"],
            "measurement_status": result["measurement_status"],
        }

    @staticmethod
    def _capture_baseline(
        summary: ServiceTransformationSummary,
        *,
        organization_id: int,
        days: int,
    ) -> dict:
        """Extract a bounded, read-only baseline snapshot to persist verbatim.

        Only derived aggregate metrics and the metric/measurement status
        fields are captured — never customer message bodies, PII, or
        employee-level records.
        """
        value = summary.value_realization
        return {
            "organization_id": organization_id,
            "window_days": days,
            "observed_at": summary.generated_at.isoformat(),
            "current_window_start": summary.window.current_start.isoformat(),
            "current_window_end": summary.window.current_end.isoformat(),
            # Flat engine inputs (the persisted snapshot and the engine input
            # are literally the same dict — one auditable shape).
            "tickets_resolved": summary.service_volume.tickets_resolved,
            "reopened_tickets": summary.sla.reopened_tickets,
            "total_sla_breaches": summary.sla.total_sla_breaches,
            "agent_runs": summary.ai_adoption.agent_runs,
            "autonomous_executions": summary.ai_adoption.autonomous_executions,
            "human_approval_required": (
                summary.ai_adoption.human_approval_required
            ),
            "knowledge_specialist_runs": (
                summary.specialist_usage.knowledge_specialist_runs
            ),
            "rates": {
                "autonomous_execution_rate": (
                    summary.ai_adoption.autonomous_execution_rate
                ),
                "human_approval_rate": summary.ai_adoption.human_approval_rate,
                "knowledge_usage_rate": (
                    summary.specialist_usage.knowledge_usage_rate
                ),
                "reopen_rate": summary.sla.reopen_rate,
            },
            "value": {
                "estimated_minutes_saved": value.estimated_minutes_saved,
                "estimated_hours_saved": value.estimated_hours_saved,
                "estimated_labor_savings_usd": value.estimated_labor_savings_usd,
                "agent_ai_cost_usd": value.agent_ai_cost_usd,
                "estimated_net_savings_usd": value.estimated_net_savings_usd,
                "pricing_configured": value.pricing_configured,
                "measurement_status": value.measurement_status,
                "minimum_autonomous_samples": value.minimum_autonomous_samples,
                "sample_size_sufficient": value.sample_size_sufficient,
                "roi_percent": value.roi_percent,
            },
        }