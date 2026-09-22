from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import record_ticket_routed
from app.models.automation_rule import AutomationRule
from app.models.service_queue import ServiceQueue
from app.models.ticket import Ticket
from app.repositories.automation_rule_repository import AutomationRuleRepository
from app.repositories.service_queue_repository import ServiceQueueRepository
from app.services.ticket_sla_service import TicketSLAService

log = get_logger(__name__)

RoutingSource = Literal["manual", "automation", "default", "ai_suggestion"]


class TicketRoutingService:
    """Deterministic routing for newly created/synced tickets."""

    @staticmethod
    def _rule_matches(
        rule: AutomationRule,
        ticket: Ticket,
    ) -> bool:
        """Evaluate a rule against a ticket (same semantics as AutomationService)."""
        conditions = rule.conditions or {}

        text = (f"{ticket.subject} {ticket.description}").lower()

        any_keywords = conditions.get("any_keywords", [])
        if any_keywords and not any(
            str(keyword).lower() in text for keyword in any_keywords
        ):
            return False

        priorities = conditions.get("priorities", [])
        if priorities and ticket.priority not in priorities:
            return False

        sources = conditions.get("sources", [])
        return not (sources and ticket.source not in sources)

    @classmethod
    async def _apply_automation_routing(
        cls,
        db: AsyncSession,
        ticket: Ticket,
        organization_id: int,
    ) -> ServiceQueue | None:
        """Find the first matching automation rule with a valid queue key."""
        rules = await AutomationRuleRepository.get_active_for_event_for_tenant(
            db,
            organization_id=organization_id,
            event_type="ticket.created",
        )

        for rule in rules:
            if not cls._rule_matches(rule, ticket):
                continue

            queue_key = (rule.actions or {}).get("service_queue_key")
            if queue_key is None:
                continue

            queue = await ServiceQueueRepository.get_by_key_for_tenant(
                db,
                key=queue_key,
                organization_id=organization_id,
            )

            if queue is None or not queue.active:
                log.warning(
                    "automation_routing_invalid_queue",
                    organization_id=organization_id,
                    rule_id=rule.id,
                    queue_key=queue_key,
                )
                continue

            return queue

        return None

    @classmethod
    async def route_new_ticket(
        cls,
        db: AsyncSession,
        ticket: Ticket,
    ) -> tuple[bool, RoutingSource]:
        """Route a newly created/synced ticket.

        Returns (routed, source). The ticket is mutated but NOT committed here.
        """
        organization_id = ticket.organization_id
        if organization_id is None:
            return False, "default"

        # 1. Deterministic automation routing
        queue = await cls._apply_automation_routing(db, ticket, organization_id)
        source: RoutingSource = "automation"

        # 2. Tenant default queue
        if queue is None:
            queue = await ServiceQueueRepository.get_default_for_tenant(
                db,
                organization_id=organization_id,
            )
            source = "default"

        # 3. No queue configured: safe no-op
        if queue is None:
            ticket.routing_source = None
            ticket.routed_at = None
            await TicketSLAService.ensure_ticket_policy_and_deadlines(db, ticket)
            return False, "default"

        ticket.service_queue_id = queue.id
        ticket.routing_source = source
        ticket.routed_at = datetime.now(UTC)
        await TicketSLAService.ensure_ticket_policy_and_deadlines(db, ticket)
        record_ticket_routed(routing_source=source)
        return True, source

    @classmethod
    async def apply_queue_to_ticket(
        cls,
        db: AsyncSession,
        ticket: Ticket,
        queue: ServiceQueue | None,
        *,
        source: RoutingSource,
    ) -> None:
        """Apply a queue to a ticket and recalculate SLA safely.

        The SLA clock is never reset to now; deadlines are recomputed from
        ticket.created_at.
        """
        ticket.service_queue_id = queue.id if queue else None
        ticket.routing_source = source
        ticket.routed_at = datetime.now(UTC)
        await TicketSLAService.ensure_ticket_policy_and_deadlines(db, ticket)
        if queue is not None:
            record_ticket_routed(routing_source=source)
