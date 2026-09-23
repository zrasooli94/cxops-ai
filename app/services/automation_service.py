from typing import ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.automation_rule import AutomationRule
from app.models.ticket import Ticket
from app.models.ticket_event import TicketEvent
from app.repositories.automation_rule_repository import AutomationRuleRepository
from app.repositories.ticket_event_repository import TicketEventRepository
from app.repositories.ticket_repository import TicketRepository
from app.services.ticket_routing_service import TicketRoutingService

log = get_logger(__name__)


class AutomationService:
    ALLOWED_ACTION_FIELDS: ClassVar[set[str]] = {
        "category",
        "assigned_team",
        "priority",
        "status",
    }

    ROUTING_ACTION_FIELDS: ClassVar[set[str]] = {
        "service_queue_key",
    }

    SLA_EVENT_TYPES: ClassVar[set[str]] = {
        "sla.first_response.due_soon",
        "sla.first_response.breached",
        "sla.resolution.due_soon",
        "sla.resolution.breached",
    }

    SLA_ALLOWED_ACTION_FIELDS: ClassVar[set[str]] = {
        "priority",
        "service_queue_key",
        "category",
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

    @classmethod
    def allowed_action_fields_for_event(cls, event_type: str) -> set[str]:
        if event_type in cls.SLA_EVENT_TYPES:
            return cls.SLA_ALLOWED_ACTION_FIELDS
        return cls.ALLOWED_ACTION_FIELDS

    @classmethod
    async def _apply_field_changes(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        changes: dict,
        organization_id: int,
    ) -> None:
        """Apply automation field changes through the ticket lifecycle.

        Ensures priority/status mutations keep SLA deadlines, resolution
        cycles, and conversation metadata consistent.
        """
        from app.services.conversation_ingestion_service import (
            ConversationIngestionService,
        )
        from app.services.ticket_sla_service import TicketSLAService

        old_status = ticket.status

        for field, value in changes.items():
            setattr(ticket, field, value)

        # Status lifecycle
        if "status" in changes:
            new_status = ticket.status
            status_changed_to_resolved = (
                new_status in ("solved", "closed") and old_status not in ("solved", "closed")
            )
            status_changed_from_resolved = (
                new_status not in ("solved", "closed") and old_status in ("solved", "closed")
            )
            if status_changed_to_resolved:
                await TicketSLAService.record_resolution(db, ticket)
            elif status_changed_from_resolved:
                ticket.resolution_sla_cycle += 1
                await TicketSLAService.handle_reopen(db, ticket)

        # Priority lifecycle for active tickets
        if "priority" in changes and ticket.status not in ("solved", "closed"):
            await TicketSLAService.recalculate_for_priority_change(db, ticket)

        await db.commit()
        await db.refresh(ticket)

        # Keep conversation metadata aligned.
        await ConversationIngestionService.sync_ticket_conversation(
            db,
            ticket=ticket,
            organization_id=organization_id,
        )

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

            allowed_fields = AutomationService.allowed_action_fields_for_event(
                event.event_type,
            )
            changes = {
                key: value
                for key, value in (rule.actions or {}).items()
                if key in allowed_fields and key not in AutomationService.ROUTING_ACTION_FIELDS
            }

            if changes:
                await AutomationService._apply_field_changes(
                    db,
                    ticket=ticket,
                    changes=changes,
                    organization_id=organization_id,
                )

            routing_key = (rule.actions or {}).get("service_queue_key")
            if routing_key is not None and "service_queue_key" in allowed_fields:
                from app.repositories.service_queue_repository import (
                    ServiceQueueRepository,
                )

                queue = await ServiceQueueRepository.get_by_key_for_tenant(
                    db,
                    key=routing_key,
                    organization_id=organization_id,
                )

                if queue is not None and queue.active:
                    await TicketRoutingService.apply_queue_to_ticket(
                        db,
                        ticket=ticket,
                        queue=queue,
                        source="automation",
                    )
                    await db.commit()
                    await db.refresh(ticket)
                else:
                    log.warning(
                        "automation_routing_invalid_queue",
                        organization_id=organization_id,
                        rule_id=rule.id,
                        queue_key=routing_key,
                    )

            break

        return await TicketEventRepository.mark_processed(
            db=db,
            event=event,
        )
