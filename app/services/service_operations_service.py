from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.service_operations_repository import (
    ServiceOperationsRepository,
)
from app.repositories.service_queue_repository import ServiceQueueRepository
from app.services.ticket_sla_service import TicketSLAService


class ServiceOperationsService:
    """Read-model assembly for the service-operations work queue."""

    @classmethod
    async def list_queue_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        queue_id: int | None = None,
        status: str | None = None,
        priority: str | None = None,
        sla_state: str | None = None,
        needs_response: bool | None = None,
        unassigned: bool | None = None,
        assignee_subject: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> list[dict]:
        if now is None:
            now = datetime.now(UTC)

        rows = await ServiceOperationsRepository.list_operations_for_tenant(
            db,
            organization_id=organization_id,
            queue_id=queue_id,
            status=status,
            priority=priority,
            sla_state=sla_state,
            needs_response=needs_response,
            unassigned=unassigned,
            assignee_subject=assignee_subject,
            search=search,
            limit=limit,
            offset=offset,
        )

        ticket_ids = [t.id for t, *_ in rows]
        ai_suggestions = await cls._ai_suggestions_for_tickets(
            db,
            ticket_ids=ticket_ids,
            organization_id=organization_id,
        )

        results = []
        for ticket, queue, conversation, row_needs_response in rows:
            first_state = TicketSLAService.first_response_state(ticket, now=now)
            resolution_state = TicketSLAService.resolution_state(ticket, now=now)
            overall_state = TicketSLAService.overall_state(ticket, now=now)

            item = {
                "ticket_id": ticket.id,
                "conversation_id": conversation.id if conversation else None,
                "subject": ticket.subject,
                "status": ticket.status,
                "priority": ticket.priority,
                "category": ticket.category,
                "service_queue_id": ticket.service_queue_id,
                "service_queue_name": queue.name if queue else None,
                "assigned_subject": ticket.assigned_subject,
                "is_assigned_to_me": (
                    assignee_subject is not None
                    and ticket.assigned_subject == assignee_subject
                ),
                "created_at": ticket.created_at,
                "updated_at": ticket.updated_at,
                "needs_response": row_needs_response,
                "first_response_due_at": ticket.first_response_due_at,
                "first_response_at": ticket.first_response_at,
                "first_response_sla_state": first_state,
                "resolution_due_at": ticket.resolution_due_at,
                "resolved_at": ticket.resolved_at,
                "resolution_sla_state": resolution_state,
                "overall_sla_state": overall_state,
                "routing_source": ticket.routing_source,
                "ai_routing_suggestion": ai_suggestions.get(ticket.id),
            }

            if sla_state is not None and overall_state != sla_state:
                continue

            results.append(item)

        return results

    @classmethod
    async def summary_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        now: datetime | None = None,
    ) -> dict:
        if now is None:
            now = datetime.now(UTC)

        summary = await ServiceOperationsRepository.summary_for_tenant(
            db,
            organization_id=organization_id,
            now=now,
        )

        # Count by service queue (bounded to active queues with open tickets)
        queues = await ServiceQueueRepository.list_active_for_tenant(
            db,
            organization_id=organization_id,
        )
        queue_counts: dict[int, dict[str, int | str]] = {
            q.id: {"id": q.id, "name": q.name, "key": q.key, "count": 0} for q in queues
        }

        rows = await ServiceOperationsRepository.list_operations_for_tenant(
            db,
            organization_id=organization_id,
            status=None,
            limit=10000,
            offset=0,
        )
        for ticket, *_ in rows:
            if ticket.status in ("new", "open", "pending") and ticket.service_queue_id in queue_counts:
                assert ticket.service_queue_id is not None
                queue_counts[ticket.service_queue_id]["count"] = int(
                    queue_counts[ticket.service_queue_id]["count"]
                ) + 1

        summary["by_queue"] = list(queue_counts.values())
        return summary

    @classmethod
    async def _ai_suggestions_for_tickets(
        cls,
        db: AsyncSession,
        *,
        ticket_ids: list[int],
        organization_id: int,
    ) -> dict[int, dict]:
        """Map latest AgentRun.recommended_team to tenant queues deterministically.

        No LLM call; no mutation; no cross-tenant lookup; no auto-create.
        """
        if not ticket_ids:
            return {}

        suggestions: dict[int, dict] = {}
        queues = {
            q.key.lower(): q
            for q in await ServiceQueueRepository.list_active_for_tenant(
                db,
                organization_id=organization_id,
            )
        }
        # Also allow matching by queue name for convenience, but key takes precedence.
        queues_by_name = {
            q.name.lower(): q
            for q in await ServiceQueueRepository.list_active_for_tenant(
                db,
                organization_id=organization_id,
            )
        }

        for ticket_id in ticket_ids:
            run = await AgentRunRepository.get_latest_for_ticket_and_tenant(
                db,
                ticket_id=ticket_id,
                organization_id=organization_id,
            )
            if run is None or not run.recommended_team:
                continue

            normalized = run.recommended_team.strip().lower()
            queue = queues.get(normalized) or queues_by_name.get(normalized)
            if queue is None:
                continue

            suggestions[ticket_id] = {
                "queue_id": queue.id,
                "queue_key": queue.key,
                "queue_name": queue.name,
            }

        return suggestions
