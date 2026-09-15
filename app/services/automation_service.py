from typing import ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.automation_rule import AutomationRule
from app.models.ticket import Ticket
from app.models.ticket_event import TicketEvent
from app.repositories.automation_rule_repository import AutomationRuleRepository
from app.repositories.ticket_event_repository import TicketEventRepository
from app.repositories.ticket_repository import TicketRepository

log = get_logger(__name__)


class AutomationService:
    ALLOWED_ACTION_FIELDS: ClassVar[set[str]] = {
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

        text = (f"{ticket.subject} {ticket.description}").lower()

        any_keywords = conditions.get(
            "any_keywords",
            [],
        )

        if any_keywords:
            keyword_match = any(
                str(keyword).lower() in text for keyword in any_keywords
            )

            if not keyword_match:
                return False

        priorities = conditions.get(
            "priorities",
            [],
        )

        if priorities and ticket.priority not in priorities:
            return False

        sources = conditions.get(
            "sources",
            [],
        )

        return not (sources and ticket.source not in sources)

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
        *,
        event: TicketEvent,
        organization_id: int | None,
    ) -> TicketEvent:

        if event.processed:
            return event

        if event.ticket_id is None:
            return await TicketEventRepository.mark_processed(
                db=db,
                event=event,
            )

        # Tenant ownership is never guessed here: callers resolve a trusted
        # organization (e.g. the verified Zendesk webhook boundary) and the
        # ticket read is tenant-scoped. A missing/failed resolution fails
        # closed — the event is marked processed and no rule runs, mirroring
        # the prior NULL-org fail-closed behavior without the unscoped read.
        if organization_id is None:
            log.warning(
                "automation_skip_no_tenant_context",
                event_id=event.id,
                ticket_id=event.ticket_id,
            )
            return await TicketEventRepository.mark_processed(
                db=db,
                event=event,
            )

        ticket = await TicketRepository.get_by_id_for_tenant(
            db=db,
            ticket_id=event.ticket_id,
            organization_id=organization_id,
        )

        if ticket is None:
            return await TicketEventRepository.mark_processed(
                db=db,
                event=event,
            )

        rules = await AutomationRuleRepository.get_active_for_event_for_tenant(
            db=db,
            organization_id=organization_id,
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
                await TicketRepository.update_for_tenant(
                    db=db,
                    ticket=ticket,
                    changes=changes,
                    organization_id=organization_id,
                )

            break

        return await TicketEventRepository.mark_processed(
            db=db,
            event=event,
        )
