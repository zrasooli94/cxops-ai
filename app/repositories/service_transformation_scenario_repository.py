"""Tenant-scoped persistence for Service Transformation scenarios.

Every read/write binds ``organization_id`` in SQL or sets it from the caller's
tenant context; scenario rows are strictly NOT NULL on organization_id, so the
legacy NULL-org rows of older tables cannot exist here.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_transformation_scenario import ServiceTransformationScenario


class ServiceTransformationScenarioRepository:
    """CRUD helpers, all bounded to one tenant."""

    @staticmethod
    async def create_for_tenant(
        db: AsyncSession,
        *,
        data: dict,
        organization_id: int,
    ) -> ServiceTransformationScenario:
        scenario = ServiceTransformationScenario(
            organization_id=organization_id,
            name=data["name"],
            description=data.get("description"),
            window_days=data["days"],
            assumptions=data["assumptions"],
        )
        db.add(scenario)
        await db.commit()
        await db.refresh(scenario)
        return scenario

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> list[ServiceTransformationScenario]:
        result = await db.execute(
            select(ServiceTransformationScenario)
            .where(ServiceTransformationScenario.organization_id == organization_id)
            .order_by(ServiceTransformationScenario.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        *,
        scenario_id: int,
        organization_id: int,
    ) -> ServiceTransformationScenario | None:
        result = await db.execute(
            select(ServiceTransformationScenario).where(
                ServiceTransformationScenario.id == scenario_id,
                ServiceTransformationScenario.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def save(
        db: AsyncSession,
        scenario: ServiceTransformationScenario,
    ) -> ServiceTransformationScenario:
        await db.commit()
        await db.refresh(scenario)
        return scenario