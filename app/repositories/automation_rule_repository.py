from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_rule import AutomationRule


class AutomationRuleRepository:

    @staticmethod
    async def create(
        db: AsyncSession,
        rule: AutomationRule,
    ) -> AutomationRule:

        db.add(rule)

        await db.commit()
        await db.refresh(rule)

        return rule

    @staticmethod
    async def get_by_id(
        db: AsyncSession,
        rule_id: int,
    ) -> AutomationRule | None:

        result = await db.execute(
            select(AutomationRule).where(
                AutomationRule.id == rule_id
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
    ) -> list[AutomationRule]:

        result = await db.execute(
            select(AutomationRule)
            .order_by(
                AutomationRule.priority.asc(),
                AutomationRule.id.asc(),
            )
        )

        return list(result.scalars().all())

    @staticmethod
    async def get_active_for_event(
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
    async def update(
        db: AsyncSession,
        rule: AutomationRule,
        changes: dict,
    ) -> AutomationRule:

        for field, value in changes.items():
            setattr(rule, field, value)

        await db.commit()
        await db.refresh(rule)

        return rule