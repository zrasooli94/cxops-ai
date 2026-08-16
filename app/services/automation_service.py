from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_rule import AutomationRule
from app.models.ticket import Ticket
from app.models.ticket_event import TicketEvent
from app.repositories.automation_rule_repository import (
    AutomationRuleRepository,
)
from app.repositories.ticket_event_repository import (
    TicketEventRepository,
)
from app.repositories.ticket_repository import TicketRepository


class AutomationService:

    ALLOWED_ACTION_FIELDS = {
        "category",
        "assigned_team",
        "priority",
        "status",
    }

    @staticmethod
    def rule_matches(
        rule: AutomationRule,
        ticket: Ticket,
    ) -> bool:

        conditions = rule.conditions or {}

        text = (
            f"{ticket.subject} "
            f"{ticket.description}"
        ).lower()

        any_keywords = conditions.get(
            "any_keywords",
            [],
        )

        if any_keywords:
            keyword_match = any(
                str(keyword).lower() in text
                for keyword in any_keywords
            )

            if not keyword_match:
                return False

        priorities = conditions.get(
            "priorities",
            [],
        )

        if priorities:
            if ticket.priority not in priorities:
                return False

        sources = conditions.get(
            "sources",
            [],
        )

        if sources:
            if ticket.source not in sources:
                return False

        return True

    @staticmethod
    def build_changes(
        rule: AutomationRule,
    ) -> dict:

        actions = rule.actions or {}

        return {
            key: value
            for key, value in actions.items()
            if key in AutomationService.ALLOWED_ACTION_FIELDS
        }

    @staticmethod
    async def process_ticket_event(
        db: AsyncSession,
        event: TicketEvent,
    ) -> TicketEvent:

        if event.processed:
            return event

        if event.ticket_id is None:
            return await TicketEventRepository.mark_processed(
                db=db,
                event=event,
            )

        ticket = await TicketRepository.get_by_id(
            db=db,
            ticket_id=event.ticket_id,
        )

        if ticket is None:
            return await TicketEventRepository.mark_processed(
                db=db,
                event=event,
            )

        rules = await AutomationRuleRepository.get_active_for_event(
            db=db,
            event_type=event.event_type,
        )

        for rule in rules:

            if not AutomationService.rule_matches(
                rule=rule,
                ticket=ticket,
            ):
                continue

            changes = AutomationService.build_changes(
                rule=rule,
            )

            if changes:
                await TicketRepository.update(
                    db=db,
                    ticket=ticket,
                    changes=changes,
                )

            break

        return await TicketEventRepository.mark_processed(
            db=db,
            event=event,
        )