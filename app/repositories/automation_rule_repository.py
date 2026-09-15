from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_rule import AutomationRule


class AutomationRuleRepository:
    # Tenant-safe methods (use these in tenant-facing API routes and in
    # tenant-owned ticket automation)

    @staticmethod
    async def create_for_tenant(
        db: AsyncSession,
        rule: AutomationRule,
        organization_id: int,
    ) -> AutomationRule:
        rule.organization_id = organization_id

        db.add(rule)

        await db.commit()
        await db.refresh(rule)

        return rule

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        rule_id: int,
        organization_id: int,
    ) -> AutomationRule | None:

        result = await db.execute(
            select(AutomationRule).where(
                AutomationRule.id == rule_id,
                AutomationRule.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession,
        organization_id: int,
    ) -> list[AutomationRule]:

        result = await db.execute(
            select(AutomationRule)
            .where(AutomationRule.organization_id == organization_id)
            .order_by(
                AutomationRule.priority.asc(),
                AutomationRule.id.asc(),
            )
        )

        return list(result.scalars().all())

    @staticmethod
    async def get_active_for_event_for_tenant(
        db: AsyncSession,
        organization_id: int,
        event_type: str,
    ) -> list[AutomationRule]:

        result = await db.execute(
            select(AutomationRule)
            .where(
                AutomationRule.organization_id == organization_id,
                AutomationRule.enabled.is_(True),
                AutomationRule.event_type == event_type,
            )
            .order_by(
                AutomationRule.priority.asc(),
                AutomationRule.id.asc(),
            )
        )

        return list(result.scalars().all())

    @staticmethod
    async def update_for_tenant(
        db: AsyncSession,
        rule: AutomationRule,
        changes: dict,
        organization_id: int,
    ) -> AutomationRule:
        """Update only if the rule belongs to the tenant organization."""
        if rule.organization_id != organization_id:
            raise ValueError("Rule does not belong to this organization")

        for field, value in changes.items():
            setattr(rule, field, value)

        await db.commit()
        await db.refresh(rule)

        return rule

    # Internal/global methods (explicitly unscoped - retained for legacy/super
    # internal tooling only; DO NOT call from tenant-facing routes).

    @staticmethod
    async def create_unscoped(
        db: AsyncSession,
        rule: AutomationRule,
    ) -> AutomationRule:

        db.add(rule)

        await db.commit()
        await db.refresh(rule)

        return rule

    @staticmethod
    async def get_by_id_unscoped(
        db: AsyncSession,
        rule_id: int,
    ) -> AutomationRule | None:

        result = await db.execute(
            select(AutomationRule).where(AutomationRule.id == rule_id)
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def list_unscoped(
        db: AsyncSession,
    ) -> list[AutomationRule]:

        result = await db.execute(
            select(AutomationRule).order_by(
                AutomationRule.priority.asc(),
                AutomationRule.id.asc(),
            )
        )

        return list(result.scalars().all())

    @staticmethod
    async def get_active_for_event_unscoped(
        db: AsyncSession,
        event_type: str,
    ) -> list[AutomationRule]:

        result = await db.execute(
            select(AutomationRule)
            .where(
                AutomationRule.enabled.is_(True),
                AutomationRule.event_type == event_type,
            )
            .order_by(
                AutomationRule.priority.asc(),
                AutomationRule.id.asc(),
            )
        )

        return list(result.scalars().all())

    @staticmethod
    async def update_unscoped(
        db: AsyncSession,
        rule: AutomationRule,
        changes: dict,
    ) -> AutomationRule:

        for field, value in changes.items():
            setattr(rule, field, value)

        await db.commit()
        await db.refresh(rule)

        return rule