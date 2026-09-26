"""Tenant-scoped persistence for Service Transformation experiments.

Every read/write binds ``organization_id`` in SQL or sets it from the caller's
tenant context; experiment rows are strictly NOT NULL on organization_id.
``created_by_subject`` is persisted from the authenticated principal — the
experiment table does not carry the Phase 1N scenario-repository quirk of
dropping it.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_transformation_experiment import (
    ServiceTransformationExperiment,
)


class ServiceTransformationExperimentRepository:
    """CRUD + row-locking helpers, all bounded to one tenant."""

    @staticmethod
    async def create_for_tenant(
        db: AsyncSession,
        *,
        data: dict,
        organization_id: int,
        created_by_subject: str | None = None,
    ) -> ServiceTransformationExperiment:
        experiment = ServiceTransformationExperiment(
            organization_id=organization_id,
            name=data["name"],
            description=data.get("description"),
            scope_type=data["scope_type"],
            scope_key=data.get("scope_key"),
            baseline_window_days=data["baseline_window_days"],
            measurement_window_days=data["measurement_window_days"],
            planned_start_at=data.get("planned_start_at"),
            planned_end_at=data.get("planned_end_at"),
            hypothesis=data["hypothesis"],
            target_metrics=data["target_metrics"],
            source_scenario_id=data.get("source_scenario_id"),
            source_scenario_snapshot=data.get("source_scenario_snapshot"),
            created_by_subject=created_by_subject,
        )
        db.add(experiment)
        await db.commit()
        await db.refresh(experiment)
        return experiment

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> list[ServiceTransformationExperiment]:
        result = await db.execute(
            select(ServiceTransformationExperiment)
            .where(
                ServiceTransformationExperiment.organization_id == organization_id
            )
            .order_by(ServiceTransformationExperiment.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        *,
        experiment_id: int,
        organization_id: int,
    ) -> ServiceTransformationExperiment | None:
        result = await db.execute(
            select(ServiceTransformationExperiment).where(
                ServiceTransformationExperiment.id == experiment_id,
                ServiceTransformationExperiment.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id_for_tenant_locked(
        db: AsyncSession,
        *,
        experiment_id: int,
        organization_id: int,
    ) -> ServiceTransformationExperiment | None:
        """Read the row with ``SELECT ... FOR UPDATE`` so state transitions are
        serialized: a concurrent capture-baseline / start / complete /
        cancel / archive cannot observe a stale status. The lock is taken only
        after the tenant boundary is applied.
        """
        result = await db.execute(
            select(ServiceTransformationExperiment)
            .where(
                ServiceTransformationExperiment.id == experiment_id,
                ServiceTransformationExperiment.organization_id == organization_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def save(
        db: AsyncSession,
        experiment: ServiceTransformationExperiment,
    ) -> ServiceTransformationExperiment:
        await db.commit()
        await db.refresh(experiment)
        return experiment