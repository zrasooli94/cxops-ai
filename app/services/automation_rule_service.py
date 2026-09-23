from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_rule import AutomationRule
from app.repositories.automation_rule_repository import (
    AutomationRuleRepository,
)
from app.schemas.automation_rule import (
    AutomationRuleCreate,
    AutomationRuleUpdate,
)
from app.services.automation_service import AutomationService


class InvalidAutomationRuleError(ValueError):
    pass


class AutomationRuleService:
    SLA_EVENT_TYPES: set[str] = AutomationService.SLA_EVENT_TYPES
    SLA_ALLOWED_ACTION_FIELDS: set[str] = AutomationService.SLA_ALLOWED_ACTION_FIELDS

    @classmethod
    def _validate_sla_rule(cls, event_type: str, actions: dict) -> None:
        if event_type not in cls.SLA_EVENT_TYPES:
            return

        for key in actions:
            if key not in cls.SLA_ALLOWED_ACTION_FIELDS:
                raise InvalidAutomationRuleError(
                    f"SLA escalation rule action '{key}' is not allowed"
                )

        if "priority" in actions:
            priority = actions["priority"]
            if priority not in {"low", "normal", "high", "urgent"}:
                raise InvalidAutomationRuleError(
                    f"Invalid SLA escalation priority: {priority}"
                )

        if "service_queue_key" in actions:
            queue_key = actions["service_queue_key"]
            if not isinstance(queue_key, str) or not queue_key.strip():
                raise InvalidAutomationRuleError(
                    "SLA escalation service_queue_key must be a non-empty string"
                )

    @staticmethod
    async def create_for_tenant(
        db: AsyncSession,
        data: AutomationRuleCreate,
        organization_id: int,
    ) -> AutomationRule:

        AutomationRuleService._validate_sla_rule(
            data.event_type,
            data.actions,
        )

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

        event_type = changes.get("event_type", rule.event_type)
        actions = changes.get("actions", rule.actions)
        AutomationRuleService._validate_sla_rule(
            event_type,
            actions,
        )

        return await AutomationRuleRepository.update_for_tenant(
            db=db,
            rule=rule,
            changes=changes,
            organization_id=organization_id,
        )
