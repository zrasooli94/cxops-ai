"""Backfill service-operations fields for legacy tickets.

Dry-run by default. Use --apply to mutate. Optionally filter by organization.
"""
import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_engine
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.ticket import Ticket
from app.repositories.service_queue_repository import ServiceQueueRepository
from app.repositories.sla_policy_repository import SLAPolicyRepository
from app.services.ticket_sla_service import TicketSLAService

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backfill_service_operations")

OPEN_TICKET_STATUSES = ("new", "open", "pending")
RESOLVED_TICKET_STATUSES = ("solved", "closed")


async def _iter_legacy_tickets(
    db: AsyncSession,
    *,
    organization_id: int | None,
    batch_size: int,
):
    stmt = select(Ticket).where(
        Ticket.service_queue_id.is_(None),
        Ticket.sla_policy_id.is_(None),
    )
    if organization_id is not None:
        stmt = stmt.where(Ticket.organization_id == organization_id)

    stmt = stmt.order_by(Ticket.id.asc()).limit(batch_size)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _find_earliest_outbound_response(
    db: AsyncSession,
    *,
    ticket: Ticket,
):
    """Infer first_response_at from existing delivered public outbound messages."""
    conversation = await db.execute(
        select(Conversation).where(
            Conversation.ticket_id == ticket.id,
            Conversation.organization_id == ticket.organization_id,
        )
    )
    conv = conversation.scalar_one_or_none()
    if conv is None:
        return None

    result = await db.execute(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conv.id,
            ConversationMessage.organization_id == ticket.organization_id,
            ConversationMessage.direction == "outbound",
            ConversationMessage.visibility == "public",
            (
                ConversationMessage.delivery_status.is_(None)
                | (ConversationMessage.delivery_status == "sent")
            ),
            ConversationMessage.sent_at.isnot(None),
        )
        .order_by(ConversationMessage.sent_at.asc(), ConversationMessage.id.asc())
        .limit(1)
    )
    message = result.scalar_one_or_none()
    return message.sent_at if message else None


async def backfill(
    *,
    organization_id: int | None,
    batch_size: int,
    apply: bool,
) -> dict:

    async with async_engine.connect():
        pass  # ensure engine is warm

    stats = {"examined": 0, "updated": 0, "skipped": 0, "errors": 0}

    async with AsyncSession(async_engine) as db:
        while True:
            tickets = await _iter_legacy_tickets(
                db,
                organization_id=organization_id,
                batch_size=batch_size,
            )
            if not tickets:
                break

            for ticket in tickets:
                stats["examined"] += 1
                try:
                    changed = await _process_ticket(db, ticket, apply=apply)
                    if changed:
                        stats["updated"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception as exc:
                    stats["errors"] += 1
                    log.exception(
                        "backfill_ticket_error",
                        ticket_id=ticket.id,
                        error=str(exc),
                    )

            if apply:
                await db.commit()
            else:
                await db.rollback()

            if len(tickets) < batch_size:
                break

    return stats


async def _process_ticket(
    db: AsyncSession,
    ticket: Ticket,
    *,
    apply: bool,
) -> bool:
    original_queue_id = ticket.service_queue_id
    original_policy_id = ticket.sla_policy_id
    original_first_response_at = ticket.first_response_at
    original_resolved_at = ticket.resolved_at

    # Default queue assignment (optional)
    default_queue = await ServiceQueueRepository.get_default_for_tenant(
        db,
        organization_id=ticket.organization_id,
    )

    if default_queue is not None:
        ticket.service_queue_id = default_queue.id
        ticket.routing_source = "default"
        ticket.routed_at = datetime.now(UTC)

    # Choose SLA policy
    policy = None
    if default_queue is not None and default_queue.sla_policy_id is not None:
        policy = await SLAPolicyRepository.get_by_id_for_tenant(
            db,
            policy_id=default_queue.sla_policy_id,
            organization_id=ticket.organization_id,
        )
    if policy is None:
        policy = await SLAPolicyRepository.get_default_for_tenant(
            db,
            organization_id=ticket.organization_id,
        )

    if policy is not None:
        ticket.sla_policy_id = policy.id
        await TicketSLAService.apply_policy_to_ticket(db, ticket, policy)

    # Infer first response
    first_response = await _find_earliest_outbound_response(db, ticket=ticket)
    if first_response is not None:
        ticket.first_response_at = first_response
        ticket.first_response_due_at = None

    # Infer resolution only from trustworthy status (no updated_at guessing)
    if ticket.status in RESOLVED_TICKET_STATUSES and ticket.resolved_at is None:
        ticket.resolved_at = ticket.updated_at
        ticket.resolution_due_at = None

    changed = (
        ticket.service_queue_id != original_queue_id
        or ticket.sla_policy_id != original_policy_id
        or ticket.first_response_at != original_first_response_at
        or ticket.resolved_at != original_resolved_at
    )

    if apply and changed:
        db.add(ticket)

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill service-operations fields for legacy tickets"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run)",
    )
    parser.add_argument(
        "--organization-id",
        type=int,
        default=None,
        help="Limit backfill to one organization",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Tickets to process per batch",
    )
    args = parser.parse_args()

    stats = asyncio.run(
        backfill(
            organization_id=args.organization_id,
            batch_size=args.batch_size,
            apply=args.apply,
        )
    )

    log.info("backfill_complete", stats=stats, apply=args.apply)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
