"""Backfill canonical conversations for tickets that predate Phase 1F.

Usage:
    python -m scripts.backfill_ticket_conversations [--apply] [--organization-id N]

Dry-run by default: it reports how many tickets lack a conversation and a
taste of the ticket ids it would process, without writing anything. Pass
``--apply`` to persist. Optionally restrict to a single organization with
``--organization-id``.

Each ticket without a conversation gets:
  - the canonical conversation row (provider ``zendesk``/``cxops`` exactly as
    runtime sync would create it), and
  - the deterministic description-initial inbound message.

No provider API calls are made; Zendesk tickets whose comments were never
synced here get the synthetic description-initial (their full comment history
is backfilled independently by webhook/manual-sync triggers on next contact).
The run is idempotent: tickets that already carry a conversation are skipped,
and re-running after ``--apply`` converges to the same rows.

Ticket ids and counts are logged; ticket subjects, email addresses and message
bodies are never logged.
"""

import argparse
import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.ticket import Ticket
from app.services.conversation_ingestion_service import ConversationIngestionService

log = get_logger(__name__)

BATCH_SIZE = 200

DRY_RUN_SAMPLE_TICKETS = 5


async def _organization_exists(db, organization_id: int) -> bool:
    from app.models.organization import Organization

    result = await db.execute(
        select(Organization.id).where(Organization.id == organization_id)
    )
    return result.scalar_one_or_none() is not None


async def backfill(
    *,
    apply: bool = False,
    organization_id: int | None = None,
) -> tuple[int, int, int, int, int, list[int]]:
    """Return ticket accounting for the run.

    Returns ``(scanned, already, created, skipped_no_org, skipped_unresolvable,
    sample_planned)``. In dry-run ``created`` holds the would-create count.
    """
    scanned = 0
    already = 0
    created = 0
    skipped_no_org = 0
    skipped_legacy = 0
    sample_planned: list[int] = []

    async with AsyncSessionLocal() as db:
        if organization_id is not None and not await _organization_exists(
            db, organization_id
        ):
            raise SystemExit(f"error: organization {organization_id} does not exist")

        offset = 0
        while True:
            stmt = select(Ticket.id, Ticket.organization_id)
            if organization_id is not None:
                stmt = stmt.where(Ticket.organization_id == organization_id)
            stmt = stmt.order_by(Ticket.id).limit(BATCH_SIZE).offset(offset)
            result = await db.execute(stmt)
            rows = result.all()

            if not rows:
                break

            scanned += len(rows)
            ticket_ids = [row[0] for row in rows]

            existing = await db.execute(
                select(Conversation.ticket_id).where(
                    Conversation.ticket_id.in_(ticket_ids)
                )
            )
            existing_ticket_ids = set(existing.scalars().all())

            already += len(ticket_ids) - len(existing_ticket_ids)

            missing_ids = [t for t in ticket_ids if t not in existing_ticket_ids]
            if not missing_ids:
                offset += BATCH_SIZE
                continue

            tickets_result = await db.execute(
                select(Ticket).where(Ticket.id.in_(missing_ids))
            )
            tickets = {t.id: t for t in tickets_result.scalars().all()}

            for ticket_id in missing_ids:
                ticket = tickets.get(ticket_id)
                if ticket is None:
                    skipped_legacy += 1
                    continue
                if ticket.organization_id is None:
                    skipped_no_org += 1
                    continue

                if not apply:
                    created += 1
                    if len(sample_planned) < DRY_RUN_SAMPLE_TICKETS:
                        sample_planned.append(ticket.id)
                    continue

                conversation = await ConversationIngestionService.sync_ticket_conversation(
                    db,
                    ticket=ticket,
                    organization_id=ticket.organization_id,
                )
                await ConversationIngestionService.ingest_initial_message(
                    db,
                    conversation=conversation,
                    ticket=ticket,
                    organization_id=ticket.organization_id,
                )
                created += 1

            offset += BATCH_SIZE

    return scanned, already, created, skipped_no_org, skipped_legacy, sample_planned


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill canonical conversations for tickets that predate Phase 1F. "
            "Dry-run by default; pass --apply to persist."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the backfill instead of only reporting it",
    )
    parser.add_argument(
        "--organization-id",
        default=None,
        type=int,
        help="Restrict the backfill to a single organization id",
    )
    args = parser.parse_args()

    if args.organization_id is not None and args.organization_id <= 0:
        parser.error("--organization-id must be a positive integer")

    scanned, already, created, skipped_no_org, skipped_legacy, sample_planned = (
        asyncio.run(backfill(apply=args.apply, organization_id=args.organization_id))
    )

    if args.apply:
        log.info(
            "backfill_complete",
            apply=True,
            tickets_scanned=scanned,
            already_had_conversation=already,
            created=created,
            skipped_no_org=skipped_no_org,
            skipped_unresolvable=skipped_legacy,
        )
        print(f"backfill complete: {created} conversation(s) created")
    else:
        log.info(
            "backfill_dry_run",
            apply=False,
            tickets_scanned=scanned,
            already_had_conversation=already,
            would_create=created,
            skipped_no_org=skipped_no_org,
            skipped_unresolvable=skipped_legacy,
            preview=sample_planned,
        )
        print(
            f"dry run: {created} ticket(s) would get a conversation "
            f"(out of {scanned} scanned); pass --apply to persist"
        )


if __name__ == "__main__":
    main()