from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_rule import AutomationRule
from app.repositories.automation_rule_repository import (
    AutomationRuleRepository,
)
from app.schemas.automation_rule import (
    AutomationRuleCreate,
    AutomationRuleUpdate,
)


class AutomationRuleService:
    @staticmethod
    async def create(
        db: AsyncSession,
        data: AutomationRuleCreate,
    ) -> AutomationRule:

        rule = AutomationRule(
            name=data.name,
            event_type=data.event_type,
            priority=data.priority,
            enabled=data.enabled,
            conditions=data.conditions,
            actions=data.actions,
        )

        return await AutomationRuleRepository.create(
            db=db,
            rule=rule,
        )

    @staticmethod
    async def update(
        db: AsyncSession,
        rule_id: int,
        data: AutomationRuleUpdate,
    ) -> AutomationRule | None:

        rule = await AutomationRuleRepository.get_by_id(
            db=db,
            rule_id=rule_id,
        )

        if rule is None:
            return None

        changes = data.model_dump(
            exclude_unset=True,
        )

        return await AutomationRuleRepository.update(
            db=db,
            rule=rule,
            changes=changes,
        )
