"""Service-layer orchestration for Service Transformation experiments.

Tenancy is always resolved from the caller's ``CurrentTenant``; the service
never accepts an ``organization_id`` from a client payload. All state
transitions run under ``SELECT ... FOR UPDATE`` (``get_by_id_for_tenant_locked``)
and re-check the status inside the lock, so concurrent transitions cannot
observe a stale status or double-capture a baseline.

Baseline, observed-outcome, and comparison data are always computed
server-side: the baseline is captured from live Phase 1L-compatible telemetry,
the observed outcome is measured from the same telemetry over the actual run
window, and ``outcome_comparison`` is produced by the pure deterministic
engine with its persisted limitations. The simulation engine is never used to
produce observed results, projected value is never presented as realized
value, and ``created_by_subject`` is persisted on every experiment row.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_transformation_experiment import (
    ServiceTransformationExperiment,
)
from app.repositories.service_queue_repository import ServiceQueueRepository
from app.repositories.service_transformation_experiment_repository import (
    ServiceTransformationExperimentRepository,
)
from app.repositories.service_transformation_scenario_repository import (
    ServiceTransformationScenarioRepository,
)
from app.schemas.service_transformation_experiment import (
    ServiceTransformationExperimentCreate,
)
from app.services.service_transformation_experiment_engine import (
    SERVICE_TRANSFORMATION_EXPERIMENT_VERSION,
    compare_outcomes,
)
from app.services.service_transformation_experiment_measurement import (
    SCOPE_TYPE_QUEUE,
    metrics_snapshot_for_window,
)

STATUS_DRAFT = "draft"
STATUS_READY = "ready"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_ARCHIVED = "archived"

_SCENARIO_STATUS_EVALUATED = "evaluated"


class ExperimentNotFoundError(Exception):
    """The experiment does not exist for the caller's tenant."""


class ExperimentStateError(Exception):
    """The requested transition is invalid for the experiment's current status."""


class ServiceTransformationExperimentService:
    """Tenant-scoped experiment lifecycle, measurement, and outcome evaluation."""

    @staticmethod
    async def create_for_tenant(
        db: AsyncSession,
        *,
        data: ServiceTransformationExperimentCreate,
        organization_id: int,
        created_by_subject: str | None = None,
    ) -> ServiceTransformationExperiment:
        if data.scope_type == SCOPE_TYPE_QUEUE:
            queue = await ServiceQueueRepository.get_by_key_for_tenant(
                db,
                key=data.scope_key or "",
                organization_id=organization_id,
            )
            if queue is None:
                raise ValueError(
                    "scope_key does not name a queue in this organization"
                )

        source_scenario_snapshot = None
        if data.source_scenario_id is not None:
            scenario = await ServiceTransformationScenarioRepository.get_by_id_for_tenant(
                db,
                scenario_id=data.source_scenario_id,
                organization_id=organization_id,
            )
            if scenario is None:
                raise ValueError(
                    "source_scenario_id does not name a scenario in this organization"
                )
            source_scenario_snapshot = {
                "id": scenario.id,
                "name": scenario.name,
                "window_days": scenario.window_days,
                "status": scenario.status,
                "formula_version": scenario.formula_version,
                "projected_result": scenario.projected_result,
                "snapshot_taken_at": datetime.now(timezone.utc).isoformat(),
            }

        planned_start_at = _as_utc(data.planned_start_at)
        planned_end_at = _as_utc(data.planned_end_at)

        return await ServiceTransformationExperimentRepository.create_for_tenant(
            db,
            data={
                "name": data.name,
                "description": data.description,
                "scope_type": data.scope_type,
                "scope_key": data.scope_key,
                "baseline_window_days": data.baseline_window_days,
                "measurement_window_days": data.measurement_window_days,
                "planned_start_at": planned_start_at,
                "planned_end_at": planned_end_at,
                "hypothesis": data.hypothesis.model_dump(),
                "target_metrics": data.target_metrics,
                "source_scenario_id": data.source_scenario_id,
                "source_scenario_snapshot": source_scenario_snapshot,
            },
            organization_id=organization_id,
            created_by_subject=created_by_subject,
        )

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> list[ServiceTransformationExperiment]:
        return await ServiceTransformationExperimentRepository.list_for_tenant(
            db,
            organization_id=organization_id,
        )

    @staticmethod
    async def get_for_tenant(
        db: AsyncSession,
        *,
        experiment_id: int,
        organization_id: int,
    ) -> ServiceTransformationExperiment | None:
        return await ServiceTransformationExperimentRepository.get_by_id_for_tenant(
            db,
            experiment_id=experiment_id,
            organization_id=organization_id,
        )

    @staticmethod
    async def _queue_id_for_tenant_locked(
        db: AsyncSession,
        experiment: ServiceTransformationExperiment,
        *,
        organization_id: int,
    ) -> int | None:
        if experiment.scope_type != SCOPE_TYPE_QUEUE:
            return None
        if not experiment.scope_key:
            raise ExperimentStateError(
                "Experiment queue scope is missing a scope_key; cannot measure"
            )
        queue = await ServiceQueueRepository.get_by_key_for_tenant(
            db,
            key=experiment.scope_key,
            organization_id=organization_id,
        )
        if queue is None:
            raise ExperimentStateError(
                "Experiment queue no longer exists in this organization; "
                "the experiment cannot be measured"
            )
        return queue.id

    @classmethod
    async def capture_baseline_for_tenant(
        cls,
        db: AsyncSession,
        *,
        experiment_id: int,
        organization_id: int,
    ) -> ServiceTransformationExperiment:
        experiment = (
            await ServiceTransformationExperimentRepository.get_by_id_for_tenant_locked(
                db,
                experiment_id=experiment_id,
                organization_id=organization_id,
            )
        )
        if experiment is None:
            raise ExperimentNotFoundError("Experiment not found")
        if experiment.status != STATUS_DRAFT:
            raise ExperimentStateError(
                "Baseline can only be captured while the experiment is draft"
            )

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=experiment.baseline_window_days)
        queue_id = await cls._queue_id_for_tenant_locked(
            db,
            experiment,
            organization_id=organization_id,
        )

        snapshot = await metrics_snapshot_for_window(
            db,
            organization_id=organization_id,
            scope_type=experiment.scope_type,
            scope_key=experiment.scope_key,
            queue_id=queue_id,
            start=start,
            end=now,
            window_days=experiment.baseline_window_days,
            observed_at=now,
        )

        experiment.baseline_snapshot = snapshot
        experiment.baseline_captured_at = now
        experiment.status = STATUS_READY
        return await ServiceTransformationExperimentRepository.save(db, experiment)

    @classmethod
    async def start_for_tenant(
        cls,
        db: AsyncSession,
        *,
        experiment_id: int,
        organization_id: int,
    ) -> ServiceTransformationExperiment:
        experiment = (
            await ServiceTransformationExperimentRepository.get_by_id_for_tenant_locked(
                db,
                experiment_id=experiment_id,
                organization_id=organization_id,
            )
        )
        if experiment is None:
            raise ExperimentNotFoundError("Experiment not found")
        if experiment.status != STATUS_READY:
            raise ExperimentStateError(
                "An experiment can only be started from the ready state"
            )

        experiment.actual_started_at = datetime.now(timezone.utc)
        experiment.status = STATUS_RUNNING
        return await ServiceTransformationExperimentRepository.save(db, experiment)

    @classmethod
    async def complete_for_tenant(
        cls,
        db: AsyncSession,
        *,
        experiment_id: int,
        organization_id: int,
    ) -> ServiceTransformationExperiment:
        experiment = (
            await ServiceTransformationExperimentRepository.get_by_id_for_tenant_locked(
                db,
                experiment_id=experiment_id,
                organization_id=organization_id,
            )
        )
        if experiment is None:
            raise ExperimentNotFoundError("Experiment not found")
        if experiment.status != STATUS_RUNNING:
            raise ExperimentStateError(
                "An experiment can only be completed from the running state"
            )
        if experiment.actual_started_at is None:
            raise ExperimentStateError(
                "A running experiment must have an actual started timestamp"
            )
        if experiment.baseline_snapshot is None:
            raise ExperimentStateError(
                "A running experiment must carry a captured baseline snapshot"
            )

        now = datetime.now(timezone.utc)
        start = experiment.actual_started_at
        queue_id = await cls._queue_id_for_tenant_locked(
            db,
            experiment,
            organization_id=organization_id,
        )

        elapsed_days = round((now - start).total_seconds() / 86400, 2)

        observed = await metrics_snapshot_for_window(
            db,
            organization_id=organization_id,
            scope_type=experiment.scope_type,
            scope_key=experiment.scope_key,
            queue_id=queue_id,
            start=start,
            end=now,
            window_days=elapsed_days,
            observed_at=now,
        )

        source_snapshot = experiment.source_scenario_snapshot
        source_evaluated = bool(
            source_snapshot
            and source_snapshot.get("status") == _SCENARIO_STATUS_EVALUATED
            and source_snapshot.get("projected_result")
        )
        projected = None
        source_window_days = None
        if source_snapshot:
            if source_evaluated:
                projected = source_snapshot.get("projected_result")
            raw_window_days = source_snapshot.get("window_days")
            if raw_window_days is not None:
                source_window_days = int(raw_window_days)

        result = compare_outcomes(
            baseline=experiment.baseline_snapshot,
            targets=dict(experiment.target_metrics),
            observed=observed,
            projected=projected,
            source_window_days=source_window_days,
            source_evaluated=source_evaluated if source_snapshot else False,
            planned_measurement_days=experiment.measurement_window_days,
        )

        experiment.observed_outcome = observed
        experiment.outcome_comparison = result
        experiment.measurement_status = result["measurement_status"]
        experiment.comparison_version = SERVICE_TRANSFORMATION_EXPERIMENT_VERSION
        experiment.actual_ended_at = now
        experiment.measured_at = now
        experiment.status = STATUS_COMPLETED
        return await ServiceTransformationExperimentRepository.save(db, experiment)

    @staticmethod
    async def cancel_for_tenant(
        db: AsyncSession,
        *,
        experiment_id: int,
        organization_id: int,
    ) -> ServiceTransformationExperiment:
        experiment = (
            await ServiceTransformationExperimentRepository.get_by_id_for_tenant_locked(
                db,
                experiment_id=experiment_id,
                organization_id=organization_id,
            )
        )
        if experiment is None:
            raise ExperimentNotFoundError("Experiment not found")
        if experiment.status not in (STATUS_DRAFT, STATUS_READY, STATUS_RUNNING):
            raise ExperimentStateError(
                "An experiment can only be cancelled from draft, ready, or running"
            )
        experiment.status = STATUS_CANCELLED
        return await ServiceTransformationExperimentRepository.save(db, experiment)

    @staticmethod
    async def archive_for_tenant(
        db: AsyncSession,
        *,
        experiment_id: int,
        organization_id: int,
    ) -> ServiceTransformationExperiment:
        experiment = (
            await ServiceTransformationExperimentRepository.get_by_id_for_tenant_locked(
                db,
                experiment_id=experiment_id,
                organization_id=organization_id,
            )
        )
        if experiment is None:
            raise ExperimentNotFoundError("Experiment not found")
        if experiment.status == STATUS_ARCHIVED:
            return experiment
        if experiment.status not in (
            STATUS_DRAFT,
            STATUS_READY,
            STATUS_COMPLETED,
            STATUS_CANCELLED,
        ):
            raise ExperimentStateError(
                "An experiment can only be archived from draft, ready, "
                "completed, or cancelled"
            )
        experiment.status = STATUS_ARCHIVED
        return await ServiceTransformationExperimentRepository.save(db, experiment)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)