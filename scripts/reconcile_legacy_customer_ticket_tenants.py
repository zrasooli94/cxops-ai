"""Reconcile legacy customer/ticket tenant ownership blocking migration 1e2a0001.

The 1E.2 migration refuses to run while rows violate the customer/ticket tenant
invariant:

    cross_org_link                      ticket.customer_id points at a customer
                                        owned by another org or a NULL-org
                                        customer, while the ticket has an org.
    customer_link_without_ticket_org    ticket has a customer_id but no org, so
                                        MATCH SIMPLE would exempt the row.
    customer_link_to_null_org_customer  referenced customer has a NULL org.

This tool inspects the precise LEGACY shape that must be repaired before that
migration can run:

    customer.organization_id IS NULL
    AND ticket.organization_id IS NULL
    AND ticket.customer_id = customer.id

It is DRY-RUN by default and only ever mutates when BOTH ``--apply`` and
``--organization-id`` are supplied. No human-only guess is ever embedded: the
operator picks the owning organization from the dry-run report.

Safety properties:
  - never prints customer PII (email/name/phone), ticket payloads, tokens,
    credentials, or secrets - ids and counts only
  - apply runs in ONE transaction; every conflict/invariant check is re-run
    inside that transaction before and after the updates, and any failure
    rolls everything back
  - no merge, no delete, no picking a winner, no partial update
  - idempotent: a second apply when already clean is a no-op
  - redundant with the migration, never a weakening of it

Usage:
    python -m scripts.reconcile_legacy_customer_ticket_tenants --dry-run
    python -m scripts.reconcile_legacy_customer_ticket_tenants \
        --organization-id 1 --apply
"""

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.ticket import Ticket

log = get_logger("reconcile_legacy")

# Mirrors alembic/versions/1e2a0001_customer_ticket_tenant_integrity.py
# `_PREFLIGHT_SQL`, so the tool reports exactly what the migration sees.
_PREFLIGHT_SQL = text(
    """
    SELECT
        COUNT(*) FILTER (
            WHERE t.customer_id IS NOT NULL
              AND t.organization_id IS NOT NULL
              AND (
                    c.organization_id IS NULL
                    OR c.organization_id <> t.organization_id
              )
        ) AS cross_org_link,
        COUNT(*) FILTER (
            WHERE t.customer_id IS NOT NULL
              AND t.organization_id IS NULL
        ) AS customer_link_without_ticket_org,
        COUNT(*) FILTER (
            WHERE t.customer_id IS NOT NULL
              AND c.organization_id IS NULL
        ) AS customer_link_to_null_org_customer
    FROM tickets AS t
    LEFT JOIN customers AS c ON c.id = t.customer_id
    """
)

_LEGACY_JOIN = (
    Ticket.customer_id.is_not(None),
    Ticket.organization_id.is_(None),
    Customer.organization_id.is_(None),
)


class ReconciliationAbort(RuntimeError):
    """Raised when a preflight/conflict/invariant check fails."""


@dataclass
class LegacySnapshot:
    ticket_ids: list[int] = field(default_factory=list)
    customer_ids: list[int] = field(default_factory=list)
    sources: dict[str, int] = field(default_factory=dict)
    tickets_with_external_id: int = 0
    tickets_without_external_id: int = 0
    customers_with_external_id: int = 0
    customers_without_external_id: int = 0
    duplicate_emails: int = 0
    duplicate_external_ids: int = 0
    organizations: list[dict[str, int | str]] = field(default_factory=list)
    preflight: dict[str, int] = field(default_factory=dict)
    # NULL-org tickets whose referenced customer is ALREADY owned. Not part of
    # the repairable legacy shape and untouched; still blocks the migration.
    non_repairable_null_org_tickets: int = 0
    projected_apply_obstacles: list[str] = field(default_factory=list)


async def _preflight_counts(db: AsyncSession) -> dict[str, int]:
    row = (await db.execute(_PREFLIGHT_SQL)).mappings().one()
    return {
        "cross_org_link": int(row["cross_org_link"]),
        "customer_link_without_ticket_org": int(
            row["customer_link_without_ticket_org"]
        ),
        "customer_link_to_null_org_customer": int(
            row["customer_link_to_null_org_customer"]
        ),
    }


async def collect_legacy_snapshot(db: AsyncSession) -> LegacySnapshot:
    """Gather the dry-run facts. Read-only; never emits customer PII."""
    joined = (
        select(Ticket, Customer)
        .join(Customer, Customer.id == Ticket.customer_id)
        .where(*_LEGACY_JOIN)
    )

    ticket_ids: list[int] = []
    customer_ids: list[int] = []
    sources: dict[str, int] = {}
    tickets_with_external_id = 0
    customers_by_id: dict[int, Customer] = {}

    result = await db.execute(joined)
    for ticket, customer in result.all():
        if ticket.id not in ticket_ids:
            ticket_ids.append(ticket.id)
            sources[ticket.source] = sources.get(ticket.source, 0) + 1
            if ticket.external_id is not None:
                tickets_with_external_id += 1
        if customer.id not in customers_by_id:
            customers_by_id[customer.id] = customer
    customer_ids = list(customers_by_id.keys())

    customers_with_external_id = sum(
        1 for c in customers_by_id.values() if c.external_id is not None
    )

    emails = [
        c.email.strip().lower() for c in customers_by_id.values()
    ]
    seen_emails: dict[str, int] = {}
    for email in emails:
        seen_emails[email] = seen_emails.get(email, 0) + 1
    duplicate_emails = sum(1 for n in seen_emails.values() if n > 1)

    external_ids: list[str | None] = [
        c.external_id.strip() if c.external_id is not None else None
        for c in customers_by_id.values()
    ]
    seen_external: dict[str, int] = {}
    for external_id in external_ids:
        if external_id is not None:
            key = external_id.lower()
            seen_external[key] = seen_external.get(key, 0) + 1
    duplicate_external_ids = sum(1 for n in seen_external.values() if n > 1)

    preflight = await _preflight_counts(db)

    non_repairable = (
        await db.execute(
            select(func.count(Ticket.id)).join(
                Customer, Customer.id == Ticket.customer_id
            ).where(
                Ticket.customer_id.is_not(None),
                Ticket.organization_id.is_(None),
                Customer.organization_id.is_not(None),
            )
        )
    ).scalar_one()

    organizations = [
        {"id": org.id, "name": org.name}
        for org in (await db.execute(select(Organization))).scalars()
    ]

    snapshot = LegacySnapshot(
        ticket_ids=ticket_ids,
        customer_ids=customer_ids,
        sources=sources,
        tickets_with_external_id=tickets_with_external_id,
        tickets_without_external_id=len(ticket_ids) - tickets_with_external_id,
        customers_with_external_id=customers_with_external_id,
        customers_without_external_id=len(customer_ids) - customers_with_external_id,
        duplicate_emails=duplicate_emails,
        duplicate_external_ids=duplicate_external_ids,
        organizations=organizations,
        preflight=preflight,
        non_repairable_null_org_tickets=int(non_repairable),
    )
    snapshot.projected_apply_obstacles = _projected_obstacles(snapshot)
    return snapshot


def _projected_obstacles(snapshot: LegacySnapshot) -> list[str]:
    """Predict, from counts only, whether apply could zero the preflight."""
    obstacles: list[str] = []
    if snapshot.preflight["cross_org_link"] > 0:
        obstacles.append(
            f"cross_org_link={snapshot.preflight['cross_org_link']} rows "
            "reference an owned/foreign customer; not repairable by this tool"
        )
    if snapshot.non_repairable_null_org_tickets > 0:
        obstacles.append(
            f"{snapshot.non_repairable_null_org_tickets} NULL-org ticket(s) "
            "reference an already-owned customer; address them explicitly"
        )
    if snapshot.duplicate_emails > 0:
        obstacles.append(
            f"{snapshot.duplicate_emails} email value(s) repeat across the "
            "legacy customers; a single target org would violate "
            "UNIQUE(organization_id, email)"
        )
    if snapshot.duplicate_external_ids > 0:
        obstacles.append(
            f"{snapshot.duplicate_external_ids} external_id value(s) repeat "
            "across the legacy customers; a single target org would violate "
            "UNIQUE(organization_id, external_id)"
        )
    return obstacles


def render_dry_run(snapshot: LegacySnapshot) -> str:
    """Human-safe dry-run report. Contains ids and counts only, never PII."""
    lines: list[str] = []
    lines.append("LEGACY CUSTOMER/TICKET TENANT RECONCILIATION — DRY-RUN")
    lines.append("= " * 40)
    lines.append(f"legacy linked tickets requiring reconciliation: {len(snapshot.ticket_ids)}")
    lines.append(f"distinct referenced legacy customers: {len(snapshot.customer_ids)}")
    lines.append("")
    lines.append("migration preflight counts (mirrors 1e2a0001):")
    lines.append(f"  cross_org_link.............................. {snapshot.preflight['cross_org_link']}")
    lines.append(f"  customer_link_without_ticket_org........... {snapshot.preflight['customer_link_without_ticket_org']}")
    lines.append(f"  customer_link_to_null_org_customer......... {snapshot.preflight['customer_link_to_null_org_customer']}")
    lines.append(f"  non-repairable NULL-org ticket(s) -> owned customer: {snapshot.non_repairable_null_org_tickets}")
    lines.append("")
    lines.append("ticket source distribution:")
    if snapshot.sources:
        for source, count in sorted(snapshot.sources.items()):
            lines.append(f"  {source}: {count}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"legacy tickets with external_id: {snapshot.tickets_with_external_id}")
    lines.append(f"legacy tickets without external_id: {snapshot.tickets_without_external_id}")
    lines.append(f"legacy customers with external_id: {snapshot.customers_with_external_id}")
    lines.append(f"legacy customers without external_id: {snapshot.customers_without_external_id}")
    lines.append("")
    lines.append("potential target-tenant uniqueness conflicts (within legacy set):")
    lines.append(f"  duplicate email values: {snapshot.duplicate_emails}")
    lines.append(f"  duplicate external_id values: {snapshot.duplicate_external_ids}")
    if snapshot.projected_apply_obstacles:
        lines.append("")
        lines.append("projected apply obstacles (must be resolved first):")
        for obstacle in snapshot.projected_apply_obstacles:
            lines.append(f"  - {obstacle}")
    lines.append("")
    lines.append("organizations present (id + name only):")
    for org in snapshot.organizations:
        lines.append(f"  {org['id']}: {org['name']}")
    return "\n".join(lines)


async def conflict_preflight(
    db: AsyncSession,
    target_organization_id: int,
    snapshot: LegacySnapshot,
) -> dict[str, Any]:
    """Return conflict facts. Raises ReconciliationAbort on any conflict."""
    organization = (
        await db.execute(
            select(Organization).where(Organization.id == target_organization_id)
        )
    ).scalar_one_or_none()
    if organization is None:
        raise ReconciliationAbort(
            f"target organization {target_organization_id} does not exist"
        )

    if not snapshot.customer_ids:
        # Nothing to assign; nothing to conflict-check.
        return {"already_clean": True}

    conflicts: dict[str, int] = {
        "existing_org_email": 0,
        "existing_org_external_id": 0,
        "duplicate_legacy_email": snapshot.duplicate_emails,
        "duplicate_legacy_external_id": snapshot.duplicate_external_ids,
        "cross_org_ticket": 0,
    }
    conflict_customer_ids: list[int] = []

    email_result = await db.execute(
        select(Customer)
        .where(
            Customer.organization_id == target_organization_id,
            func.lower(Customer.email).in_(
                select(func.lower(Customer.email)).where(
                    Customer.id.in_(snapshot.customer_ids),
                    Customer.organization_id.is_(None),
                )
            ),
        )
        .limit(1)
    )
    if email_result.scalar_one_or_none() is not None:
        conflicts["existing_org_email"] = 1

    external_result = await db.execute(
        select(Customer)
        .where(
            Customer.organization_id == target_organization_id,
            Customer.external_id.is_not(None),
            func.lower(Customer.external_id).in_(
                select(func.lower(Customer.external_id)).where(
                    Customer.id.in_(snapshot.customer_ids),
                    Customer.organization_id.is_(None),
                    Customer.external_id.is_not(None),
                )
            ),
        )
        .limit(1)
    )
    if external_result.scalar_one_or_none() is not None:
        conflicts["existing_org_external_id"] = 1

    # Relational safety: an affected customer must not be referenced by a
    # ticket owned by a DIFFERENT org - after assignment that composite FK
    # (customer_id, organization_id) could never match.
    cross_org_customers = (
        await db.execute(
            select(Ticket.customer_id)
            .where(
                Ticket.customer_id.in_(snapshot.customer_ids),
                Ticket.organization_id.is_not(None),
                Ticket.organization_id != target_organization_id,
            )
            .distinct()
        )
    ).scalars().all()
    if cross_org_customers:
        conflicts["cross_org_ticket"] = len(cross_org_customers)
        conflict_customer_ids.extend(int(c) for c in cross_org_customers)

    if any(conflicts.values()) or conflicts["duplicate_legacy_email"]:
        raise ReconciliationAbort(
            f"conflict preflight failed (counts={json.dumps(conflicts)}); "
            "aborting entirely - no merge/delete/partial update performed. "
            f"conflicts(customer_ids no PII)={sorted(set(conflict_customer_ids))}"
        )

    return {
        "already_clean": False,
        "conflicts": conflicts,
        "organization": {"id": organization.id, "name": organization.name},
    }


async def run_apply(db: AsyncSession, target_organization_id: int) -> dict[str, Any]:
    """Assign the target org to legacy customers and their linked tickets.

    Everything runs in a single session transaction (autobegun): the pre-checks,
    the row locking, and the updates. A single commit at the end makes it atomic;
    any failure rolls the whole transaction back.
    """
    try:
        snapshot = await collect_legacy_snapshot(db)

        if not snapshot.ticket_ids:
            return {
                "already_clean": True,
                "assigned_customers": 0,
                "assigned_tickets": 0,
            }

        await conflict_preflight(db, target_organization_id, snapshot)

        # Lock the affected rows so a concurrent apply cannot race us.
        locked_customers = (
            await db.execute(
                select(Customer.id)
                .where(Customer.id.in_(snapshot.customer_ids))
                .with_for_update()
            )
        ).scalars().all()
        locked_tickets = (
            await db.execute(
                select(Ticket.id)
                .where(Ticket.id.in_(snapshot.ticket_ids))
                .with_for_update()
            )
        ).scalars().all()
        if len(locked_customers) != len(snapshot.customer_ids) or len(
            locked_tickets
        ) != len(snapshot.ticket_ids):
            raise ReconciliationAbort(
                "concurrent reconciliation changed the affected row set; aborting"
            )

        # Re-run the full preflight after locking, before any update.
        await conflict_preflight(db, target_organization_id, snapshot)

        await db.execute(
            update(Customer)
            .where(Customer.id.in_(snapshot.customer_ids))
            .values(organization_id=target_organization_id)
        )
        await db.execute(
            update(Ticket)
            .where(Ticket.id.in_(snapshot.ticket_ids))
            .values(organization_id=target_organization_id)
        )

        # Post-apply invariant: all three preflight counts must be zero.
        remaining = await _preflight_counts(db)
        if any(remaining.values()):
            raise ReconciliationAbort(
                f"post-apply validation failed (remaining counts={json.dumps(remaining)}); "
                "rolling everything back - migration would still refuse"
            )

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    assigned_customers = len(snapshot.customer_ids)
    assigned_tickets = len(snapshot.ticket_ids)
    log.info(
        "reconcile_apply_success",
        target_organization_id=target_organization_id,
        assigned_customers=assigned_customers,
        assigned_tickets=assigned_tickets,
    )
    return {
        "already_clean": False,
        "assigned_customers": assigned_customers,
        "assigned_tickets": assigned_tickets,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect (default) or assign organization ownership to legacy "
            "customer/ticket pairs blocking migration 1e2a0001."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="report only; never mutates (default).",
    )
    parser.add_argument(
        "--apply",
        action="store_const",
        const=True,
        default=False,
        help="assign ownership. Requires --organization-id.",
    )
    parser.add_argument(
        "--organization-id",
        type=int,
        default=None,
        help="target organization id to assign legacy rows to.",
    )
    args = parser.parse_args(argv)
    if args.apply and args.organization_id is None:
        parser.error("--apply requires --organization-id")
    if args.organization_id is not None and args.organization_id <= 0:
        parser.error("--organization-id must be a positive integer")
    return args


async def _main(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        if args.apply:
            result = await run_apply(db, args.organization_id)
            if result["already_clean"]:
                print("already clean: no legacy customer/ticket pairs to assign.")
            else:
                print(
                    f"apply complete: {result['assigned_customers']} customer(s) "
                    f"and {result['assigned_tickets']} ticket(s) assigned to "
                    f"organization {args.organization_id} (single transaction)."
                )
            snapshot = await collect_legacy_snapshot(db)
            print("post-apply dry-run:")
            print(render_dry_run(snapshot))
            return

        snapshot = await collect_legacy_snapshot(db)
        print(render_dry_run(snapshot))
        if args.organization_id is not None:
            try:
                preflight = await conflict_preflight(
                    db, args.organization_id, snapshot
                )
            except ReconciliationAbort as exc:
                print(f"\nCONFLICT PREFLIGHT (target {args.organization_id}): {exc}")
            else:
                if preflight.get("already_clean"):
                    print("\nCONFLICT PREFLIGHT: already clean, nothing to assign.")
                else:
                    org = preflight["organization"]
                    print(
                        f"\nCONFLICT PREFLIGHT (target {args.organization_id} "
                        f"'{org['name']}'): PASS - no conflicts."
                    )


def main() -> None:
    args = parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()