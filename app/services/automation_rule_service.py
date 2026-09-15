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
    async def create_for_tenant(
        db: AsyncSession,
        data: AutomationRuleCreate,
        organization_id: int,
    ) -> AutomationRule:

        rule = AutomationRule(
            name=data.name,
            event_type=data.event_type,
            priority=data.priority,
            enabled=data.enabled,
            conditions=data.conditions,
            actions=data.actions,
        )

        return await AutomationRuleRepository.create_for_tenant(
            db=db,
            rule=rule,
            organization_id=organization_id,
        )

    @staticmethod
    async def update_for_tenant(
        db: AsyncSession,
        rule_id: int,
        data: AutomationRuleUpdate,
        organization_id: int,
    ) -> AutomationRule | None:

        rule = await AutomationRuleRepository.get_by_id_for_tenant(
            db=db,
            rule_id=rule_id,
            organization_id=organization_id,
        )

        if rule is None:
            return None

        changes = data.model_dump(
            exclude_unset=True,
        )

        return await AutomationRuleRepository.update_for_tenant(
            db=db,
            rule=rule,
            changes=changes,
            organization_id=organization_id,
        )