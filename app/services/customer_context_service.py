"""Bounded, tenant-safe customer context for Agent analysis.

CustomerContext is internal read-only context. It never exposes provider OAuth
tokens, raw webhook payloads, or hidden reasoning. It is bounded before being
injected into an LLM prompt and its digest participates in the agent analysis
fingerprint so context changes invalidate stale analyses.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.customer_identity_repository import (
    CustomerIdentityRepository,
)
from app.repositories.customer_repository import CustomerRepository
from app.repositories.ticket_repository import TicketRepository


class CustomerContextTicket(BaseModel):
    id: int
    subject: str
    status: str
    priority: str
    category: str | None
    source: str
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class CustomerContextActivity(BaseModel):
    id: str
    type: str
    source: str
    occurred_at: datetime | None
    title: str


class CustomerContextSummary(BaseModel):
    total_tickets: int
    open_tickets: int
    resolved_tickets: int
    common_category: str | None
    last_ticket_at: datetime | None
    last_interaction_at: datetime | None


class CustomerContext(BaseModel):
    customer_id: int
    display_name: str
    known_channels: list[str]
    summary: CustomerContextSummary
    recent_tickets: list[CustomerContextTicket]
    recent_activity: list[CustomerContextActivity]
    partial: bool = False
    unavailable_sources: list[str] = Field(default_factory=list)


class CustomerContextService:
    """Build a bounded CustomerContext for a tenant-owned customer."""

    MAX_RECENT_TICKETS = 5
    MAX_RECENT_ACTIVITY = 10
    MAX_TEXT_LENGTH = 500
    MAX_SERIALIZED_BYTES = 8192

    @staticmethod
    def _truncate_text(value: str | None) -> str:
        if value is None:
            return ""
        return value[: CustomerContextService.MAX_TEXT_LENGTH]

    @staticmethod
    def _ticket_to_context(ticket: Any) -> CustomerContextTicket:
        return CustomerContextTicket(
            id=ticket.id,
            subject=CustomerContextService._truncate_text(ticket.subject),
            status=ticket.status,
            priority=ticket.priority,
            category=CustomerContextService._truncate_text(ticket.category),
            source=ticket.source,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
        )

    @classmethod
    async def build_for_ticket_customer(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        customer_id: int | None,
    ) -> CustomerContext | None:
        """Build context for the customer linked to a ticket.

        ``organization_id`` comes from the trusted Agent workflow tenant.
        ``customer_id`` comes from the persisted tenant-owned ticket.

        If ``customer_id`` is None, context is absent. If the customer cannot be
        resolved inside the same tenant, an integrity error is raised.
        """
        if customer_id is None:
            return None

        customer = await CustomerRepository.get_by_id_for_tenant(
            db,
            customer_id,
            organization_id,
        )
        if customer is None:
            raise ValueError(
                "Ticket.customer_id references a customer outside the tenant."
            )

        return await cls.build_for_customer(
            db,
            organization_id=organization_id,
            customer=customer,
        )

    @classmethod
    async def build_for_customer(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        customer: Customer,
    ) -> CustomerContext:
        partial = False
        unavailable_sources: list[str] = []

        # Summary
        try:
            summary = await TicketRepository.summarize_for_customer(
                db,
                customer_id=customer.id,
                organization_id=organization_id,
            )
            common_category = await TicketRepository.common_category_for_customer(
                db,
                customer_id=customer.id,
                organization_id=organization_id,
            )
            summary_model = CustomerContextSummary(
                total_tickets=summary["total"],
                open_tickets=summary["open"],
                resolved_tickets=summary["resolved"],
                common_category=common_category,
                last_ticket_at=summary["latest_ticket_at"],
                last_interaction_at=summary["latest_interaction_at"],
            )
        except Exception:  # noqa: BLE001 -- degrade gracefully
            partial = True
            unavailable_sources.append("summary")
            summary_model = CustomerContextSummary(
                total_tickets=0,
                open_tickets=0,
                resolved_tickets=0,
                common_category=None,
                last_ticket_at=None,
                last_interaction_at=None,
            )

        # Recent tickets
        recent_tickets: list[CustomerContextTicket] = []
        try:
            tickets = await TicketRepository.list_for_customer_for_tenant(
                db,
                customer_id=customer.id,
                organization_id=organization_id,
                offset=0,
                limit=cls.MAX_RECENT_TICKETS,
            )
            recent_tickets = [cls._ticket_to_context(t) for t in tickets]
        except Exception:  # noqa: BLE001
            partial = True
            unavailable_sources.append("recent_tickets")

        # Recent activity (tickets + agent runs, newest first)
        recent_activity: list[CustomerContextActivity] = []
        try:
            tickets_for_activity = await TicketRepository.list_for_customer_for_tenant(
                db,
                customer_id=customer.id,
                organization_id=organization_id,
                offset=0,
                limit=cls.MAX_RECENT_ACTIVITY,
            )
            for ticket in tickets_for_activity:
                recent_activity.append(
                    CustomerContextActivity(
                        id=f"ticket:{ticket.id}",
                        type="ticket.created",
                        source="ticket",
                        occurred_at=ticket.created_at,
                        title=cls._truncate_text(ticket.subject),
                    )
                )

            agent_runs = await AgentRunRepository.list_for_customer_for_tenant(
                db,
                customer_id=customer.id,
                organization_id=organization_id,
                offset=0,
                limit=cls.MAX_RECENT_ACTIVITY,
            )
            for run in agent_runs:
                recent_activity.append(
                    CustomerContextActivity(
                        id=f"agent:{run.run_id}",
                        type="agent.run",
                        source="agent",
                        occurred_at=run.created_at,
                        title=f"Agent {run.action}",
                    )
                )

            recent_activity.sort(
                key=lambda item: (
                    item.occurred_at or datetime.min.replace(tzinfo=UTC),
                    item.id,
                ),
                reverse=True,
            )
            recent_activity = recent_activity[: cls.MAX_RECENT_ACTIVITY]
        except Exception:  # noqa: BLE001
            partial = True
            unavailable_sources.append("recent_activity")

        # Known channels from identities
        known_channels: list[str] = []
        try:
            identities = await CustomerIdentityRepository.list_for_customer_for_tenant(
                db,
                organization_id=organization_id,
                customer_id=customer.id,
                limit=100,
            )
            channels = {identity.provider for identity in identities}
            known_channels = sorted(channels)
        except Exception:  # noqa: BLE001
            partial = True
            unavailable_sources.append("identities")

        context = CustomerContext(
            customer_id=customer.id,
            display_name=cls._truncate_text(customer.name),
            known_channels=known_channels,
            summary=summary_model,
            recent_tickets=recent_tickets,
            recent_activity=recent_activity,
            partial=partial,
            unavailable_sources=sorted(set(unavailable_sources)),
        )

        return cls._apply_size_bounds(context)

    @classmethod
    def _apply_size_bounds(cls, context: CustomerContext) -> CustomerContext:
        """Ensure the serialized context fits within the LLM budget.

        Truncation is deterministic: trim text fields, then reduce list lengths
        while keeping newest items first.
        """
        data = context.model_dump()
        serialized = json.dumps(data, default=str, sort_keys=True).encode("utf-8")

        if len(serialized) <= cls.MAX_SERIALIZED_BYTES:
            return context

        # First pass: truncate longer text fields more aggressively.
        for ticket in data.get("recent_tickets", []):
            for key in ("subject", "category"):
                ticket[key] = ticket.get(key, "")[: cls.MAX_TEXT_LENGTH // 2]
        for activity in data.get("recent_activity", []):
            activity["title"] = activity.get("title", "")[: cls.MAX_TEXT_LENGTH // 2]
        data["display_name"] = data.get("display_name", "")[: cls.MAX_TEXT_LENGTH // 2]

        serialized = json.dumps(data, default=str, sort_keys=True).encode("utf-8")
        if len(serialized) <= cls.MAX_SERIALIZED_BYTES:
            return CustomerContext.model_validate(data)

        # Second pass: reduce list sizes, newest first.
        data["recent_tickets"] = data["recent_tickets"][:3]
        data["recent_activity"] = data["recent_activity"][:5]

        serialized = json.dumps(data, default=str, sort_keys=True).encode("utf-8")
        if len(serialized) <= cls.MAX_SERIALIZED_BYTES:
            return CustomerContext.model_validate(data)

        # Last-resort fallback: drop activity and keep minimal summary/tickets.
        data["recent_activity"] = []
        data["recent_tickets"] = data["recent_tickets"][:2]
        data["partial"] = True
        if "size_truncated" not in data.get("unavailable_sources", []):
            data["unavailable_sources"] = sorted(
                set(data.get("unavailable_sources", []) + ["size_truncated"])
            )

        return CustomerContext.model_validate(data)

    @classmethod
    def compute_digest(cls, context: CustomerContext | None) -> str:
        """Deterministic digest over the material customer context fields.

        The raw context is never logged; only this opaque digest is used in the
        agent fingerprint.
        """
        if context is None:
            return ""

        data = context.model_dump()
        payload = {
            "customer_id": data.get("customer_id"),
            "display_name": data.get("display_name"),
            "known_channels": data.get("known_channels"),
            "summary": {
                "total_tickets": data["summary"].get("total_tickets"),
                "open_tickets": data["summary"].get("open_tickets"),
                "resolved_tickets": data["summary"].get("resolved_tickets"),
                "common_category": data["summary"].get("common_category"),
                "last_interaction_at": data["summary"].get("last_interaction_at"),
            },
            "recent_tickets": [
                {
                    "id": t.get("id"),
                    "status": t.get("status"),
                    "priority": t.get("priority"),
                    "category": t.get("category"),
                    "source": t.get("source"),
                }
                for t in data.get("recent_tickets", [])
            ],
            "recent_activity": [
                {
                    "type": a.get("type"),
                    "source": a.get("source"),
                    "occurred_at": a.get("occurred_at"),
                }
                for a in data.get("recent_activity", [])
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
