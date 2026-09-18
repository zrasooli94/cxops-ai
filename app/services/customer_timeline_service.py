from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import AuthorizationContext, Capability
from app.models.agent_run import AgentRun
from app.models.ticket import Ticket
from app.models.ticket_event import TicketEvent
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.ticket_event_repository import TicketEventRepository
from app.repositories.ticket_repository import TicketRepository
from app.schemas.customer_timeline import (
    CustomerTimelineEvent,
    CustomerTimelineResponse,
)

SOURCE_TICKET: Literal["ticket"] = "ticket"
SOURCE_TICKET_EVENT: Literal["ticket_event"] = "ticket_event"
SOURCE_AGENT: Literal["agent"] = "agent"


def _ticket_entry(ticket: Ticket) -> CustomerTimelineEvent:
    """Ticket row → allowlisted timeline event (ticket.created)."""
    return CustomerTimelineEvent(
        id=f"ticket:{ticket.id}",
        type="ticket.created",
        source=SOURCE_TICKET,
        occurred_at=ticket.created_at,
        title=ticket.subject,
        summary=f"Status: {ticket.status}",
        ticket_id=ticket.id,
        metadata={
            "status": ticket.status,
            "priority": ticket.priority,
            "source": ticket.source,
        },
    )


def _ticket_event_entry(event: TicketEvent) -> CustomerTimelineEvent:
    """TicketEvent → allowlisted timeline event.

    Only ``event_type`` and ``created_at`` are exposed. ``payload`` (raw webhook
    body) is never serialized.
    """
    return CustomerTimelineEvent(
        id=f"ticket_event:{event.id}",
        type=event.event_type,
        source=SOURCE_TICKET_EVENT,
        occurred_at=event.created_at,
        title="Ticket event",
        summary=event.event_type,
        ticket_id=event.ticket_id,
        metadata={},
    )


def _agent_entry(run: AgentRun) -> CustomerTimelineEvent:
    """AgentRun → allowlisted timeline event.

    Only safe surface fields are exposed (``action``, ``status``, ``run_id``).
    Sensitive agent internals — ``reason``, ``response_draft``, ``tool_plan``,
    ``sources``, ``workflow_path``, ``reviewer_note``, ``error_message`` — are
    never serialized.
    """
    return CustomerTimelineEvent(
        id=f"agent:{run.run_id}",
        type="agent.run",
        source=SOURCE_AGENT,
        occurred_at=run.created_at,
        title=f"Agent run on ticket #{run.ticket_id}",
        summary=f"Action: {run.action}",
        ticket_id=run.ticket_id,
        agent_run_id=run.run_id,
        metadata={
            "action": run.action,
            "status": run.status,
        },
    )


class CustomerTimelineService:
    """Bounded, capability-composed customer timeline.

    The timeline is gathered per capability, so a subject without ``ticket.read``
    never receives ticket-derived entries and a subject without ``agent.run``
    never receives agent entries. Every source query carries both the customer
    and organization predicates in SQL, and each source is independently bounded
    (``offset + limit``) so no single query can fan out unbounded.

    If a source query fails internally, that source is reported in
    ``unavailable_sources`` with ``partial`` set — the endpoint never silently
    pretends a complete timeline.
    """

    @staticmethod
    def _fetch_bound(offset: int, limit: int) -> int:
        # Every source contributes at least the requested window so merging and
        # slicing at [offset, offset+limit) is exact across all sources.
        return offset + limit

    @staticmethod
    async def _build_ticket_source(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[CustomerTimelineEvent], int]:
        rows = await TicketRepository.list_for_customer_for_tenant(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
            offset=offset,
            limit=limit,
        )
        total = await TicketRepository.count_for_customer_for_tenant(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
        )
        return [_ticket_entry(row) for row in rows], total

    @staticmethod
    async def _build_ticket_event_source(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[CustomerTimelineEvent], int]:
        rows = await TicketEventRepository.list_for_customer_for_tenant(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
            offset=offset,
            limit=limit,
        )
        total = await TicketEventRepository.count_for_customer_for_tenant(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
        )
        return [_ticket_event_entry(row) for row in rows], total

    @staticmethod
    async def _build_agent_source(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[CustomerTimelineEvent], int]:
        rows = await AgentRunRepository.list_for_customer_for_tenant(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
            offset=offset,
            limit=limit,
        )
        total = await AgentRunRepository.count_for_customer_for_tenant(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
        )
        return [_agent_entry(row) for row in rows], total

    @staticmethod
    async def build_timeline(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
        authz: AuthorizationContext,
        offset: int,
        limit: int,
    ) -> CustomerTimelineResponse:
        # The caller (route) resolves the customer tenant-safely first and
        # raises a non-enumerating 404 when it does not exist.

        fetch_bound = CustomerTimelineService._fetch_bound(offset, limit)

        has_ticket_read = authz.has(Capability.TICKET_READ)
        has_agent_run = authz.has(Capability.AGENT_RUN)

        events: list[CustomerTimelineEvent] = []
        totals: dict[str, int] = {}
        unavailable_sources: list[str] = []

        if has_ticket_read:
            try:
                rows, total = await CustomerTimelineService._build_ticket_source(
                    db,
                    customer_id=customer_id,
                    organization_id=organization_id,
                    offset=0,
                    limit=fetch_bound,
                )
                events.extend(rows)
                totals[SOURCE_TICKET] = total
            except Exception:  # noqa: BLE001 -- intended: mark source unavailable
                unavailable_sources.append(SOURCE_TICKET)

            try:
                rows, total = await (
                    CustomerTimelineService._build_ticket_event_source
                )(
                    db,
                    customer_id=customer_id,
                    organization_id=organization_id,
                    offset=0,
                    limit=fetch_bound,
                )
                events.extend(rows)
                totals[SOURCE_TICKET_EVENT] = total
            except Exception:  # noqa: BLE001 -- intended: mark source unavailable
                unavailable_sources.append(SOURCE_TICKET_EVENT)

        if has_agent_run:
            try:
                rows, total = await CustomerTimelineService._build_agent_source(
                    db,
                    customer_id=customer_id,
                    organization_id=organization_id,
                    offset=0,
                    limit=fetch_bound,
                )
                events.extend(rows)
                totals[SOURCE_AGENT] = total
            except Exception:  # noqa: BLE001 -- intended: mark source unavailable
                unavailable_sources.append(SOURCE_AGENT)

        # Deterministic order: newest first, then source. Python's sort is
        # stable, and each source query returns rows newest-first, so events
        # sharing an exact (occurred_at, source) boundary keep a fixed order.
        events.sort(
            key=lambda event: (event.occurred_at, event.source),
            reverse=True,
        )

        return CustomerTimelineResponse(
            items=events[offset : offset + limit],
            total=sum(totals.values()),
            partial=len(unavailable_sources) > 0,
            unavailable_sources=sorted(unavailable_sources),
        )