"""Audit and reconcile legacy public-chat close drift (Phase 1P.7.2).

A public-chat session row can say ``closed`` while its linked ticket is still
``new``/``open``/``pending``, its linked conversation is still ``open``, or the
ticket's resolution milestone (``resolved_at``) was never recorded. That is the
documented Phase 1P.7.1 legacy drift: an older close path (or a close whose SLA
follow-up failed after the atomic core commit) left the session terminal but the
linked lifecycle inconsistent.

This tool inspects exactly that shape for ONE tenant:

    PublicChatSession.status == "closed"
    AND (
        Ticket.status NOT IN ("solved", "closed")
        OR Conversation.status != "closed"
        OR Ticket.resolved_at IS NULL
    )

It is DRY-RUN by default and only ever mutates when ``--apply`` is supplied. The
tenant is resolved from ``Organization.external_id`` - never from a numeric id
the operator types by hand - so the report scope is unambiguous.

Repair path (apply):
  - every drifted closed session is re-read tenant-scoped immediately before it
    is changed, and is defensively re-asserted to still be ``closed``; a session
    that changed state in the meantime is skipped and reported, never mutated;
  - the repair calls the EXISTING shared lifecycle
    ``PublicChatResolutionService.close_lifecycle`` - the script contains no
    lifecycle SQL of its own. Because the session is already ``closed`` the
    lifecycle takes its reconciliation branch: the linked ticket is solved and
    the linked conversation is closed in one tenant-scoped transaction, then the
    SLA resolution milestone is re-recorded if it was missing. The session row
    is not touched, so ``closed_at`` and the assignment trail are preserved and
    no new close transition is produced.

Safety properties:
  - read-only unless ``--apply`` is passed
  - candidates are ONLY sessions already in ``closed``; a live session
    (``ai_active``/``human_requested``/``human_assigned``) can never be a
    candidate and is never mutated
  - never prints tokens, token hashes, widget keys or their hashes, message
    bodies, customer text, email, IP, secrets, or DATABASE_URL - bounded
    identifiers and counts only
  - idempotent: a second ``--apply`` on an already-repaired tenant finds zero
    drift and performs no lifecycle transition (no close metric, no close log)

Usage:
    python -m scripts.reconcile_public_chat_legacy_drift --tenant rispu
    python -m scripts.reconcile_public_chat_legacy_drift --tenant rispu --apply
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.organization import Organization
from app.models.public_chat import PublicChatSession
from app.models.ticket import Ticket
from app.repositories.public_chat_repository import PublicChatRepository
from app.services.public_chat_resolution import PublicChatResolutionService
from app.services.ticket_sla_service import RESOLVED_TICKET_STATUSES

log = get_logger("reconcile_public_chat_legacy_drift")

CLOSED_STATUS = "closed"
# The metric/log actor label is bounded to "customer" or "staff"; a repair never
# performs a transition, so this label is only the safety fallback carried into
# the shared lifecycle. "staff" matches the operator-initiated repair context.
REPAIR_CLOSED_BY = "staff"

DRY_RUN_FOOTER = "DRY RUN — no changes made"


@dataclass(frozen=True)
class DriftedSession:
    """One closed session whose linked ticket/conversation/milestone drifted."""

    session_id: int
    ticket_id: int
    conversation_id: int
    ticket_status: str
    conversation_status: str
    resolved_at_present: bool


@dataclass
class DriftSnapshot:
    """Read-only audit result for one tenant."""

    tenant_external_id: str
    organization_id: int
    closed_sessions_checked: int
    drifted: list[DriftedSession] = field(default_factory=list)


@dataclass
class ApplyResult:
    """Outcome of an ``--apply`` run for one tenant."""

    tenant_external_id: str
    organization_id: int
    closed_sessions_checked: int
    drifted_before: int
    applied: int
    skipped_state_changed: list[int] = field(default_factory=list)
    drifted_after: int = 0


async def resolve_organization_id(
    db: AsyncSession,
    tenant_external_id: str,
) -> int | None:
    """Resolve the tenant from ``Organization.external_id`` (or ``None``)."""
    return (
        await db.execute(
            select(Organization.id).where(
                Organization.external_id == tenant_external_id
            )
        )
    ).scalar_one_or_none()


async def collect_drift_snapshot(
    db: AsyncSession,
    *,
    tenant_external_id: str,
    organization_id: int,
) -> DriftSnapshot:
    """Gather the drifted closed sessions for one tenant. Read-only.

    ``closed_sessions_checked`` is every ``closed`` session for the tenant;
    ``drifted`` is the subset whose linked ticket, linked conversation, or
    resolution milestone is inconsistent. Live sessions are excluded by the
    ``status == "closed"`` predicate, not by post-filtering.
    """
    closed_sessions_checked = int(
        (
            await db.execute(
                select(func.count(PublicChatSession.id)).where(
                    PublicChatSession.organization_id == organization_id,
                    PublicChatSession.status == CLOSED_STATUS,
                )
            )
        ).scalar_one()
    )

    rows = (
        await db.execute(
            select(PublicChatSession, Ticket, Conversation)
            .select_from(PublicChatSession)
            .join(
                Ticket,
                and_(
                    Ticket.id == PublicChatSession.ticket_id,
                    Ticket.organization_id == PublicChatSession.organization_id,
                ),
            )
            .join(
                Conversation,
                and_(
                    Conversation.id == PublicChatSession.conversation_id,
                    Conversation.organization_id
                    == PublicChatSession.organization_id,
                ),
            )
            .where(
                PublicChatSession.organization_id == organization_id,
                PublicChatSession.status == CLOSED_STATUS,
                or_(
                    Ticket.status.not_in(RESOLVED_TICKET_STATUSES),
                    Conversation.status != CLOSED_STATUS,
                    Ticket.resolved_at.is_(None),
                ),
            )
            .order_by(PublicChatSession.id)
        )
    ).all()

    drifted = [
        DriftedSession(
            session_id=session.id,
            ticket_id=session.ticket_id,
            conversation_id=session.conversation_id,
            ticket_status=ticket.status,
            conversation_status=conversation.status,
            resolved_at_present=ticket.resolved_at is not None,
        )
        for session, ticket, conversation in rows
    ]
    return DriftSnapshot(
        tenant_external_id=tenant_external_id,
        organization_id=organization_id,
        closed_sessions_checked=closed_sessions_checked,
        drifted=drifted,
    )


def render_dry_run(snapshot: DriftSnapshot) -> str:
    """Human-safe dry-run report: bounded ids/statuses and counts only."""
    lines: list[str] = []
    lines.append("PUBLIC-CHAT LEGACY DRIFT RECONCILIATION — DRY RUN")
    lines.append("=" * 60)
    lines.append(f"tenant (external_id): {snapshot.tenant_external_id}")
    lines.append(f"organization_id: {snapshot.organization_id}")
    lines.append(f"closed sessions checked: {snapshot.closed_sessions_checked}")
    lines.append(f"drifted sessions: {len(snapshot.drifted)}")
    if snapshot.drifted:
        lines.append("")
        lines.append("drifted closed sessions (bounded identifiers only):")
        lines.append(
            "  session_id | ticket_id | conversation_id | ticket_status | "
            "conversation_status | resolved_at_present"
        )
        for drifted in snapshot.drifted:
            lines.append(
                f"  {drifted.session_id} | {drifted.ticket_id} | "
                f"{drifted.conversation_id} | {drifted.ticket_status} | "
                f"{drifted.conversation_status} | "
                f"{drifted.resolved_at_present}"
            )
    lines.append("")
    lines.append(DRY_RUN_FOOTER)
    return "\n".join(lines)


def render_apply_result(result: ApplyResult) -> str:
    """Human-safe apply report: bounded ids and counts only."""
    lines: list[str] = []
    lines.append("PUBLIC-CHAT LEGACY DRIFT RECONCILIATION — APPLY COMPLETE")
    lines.append("=" * 60)
    lines.append(f"tenant (external_id): {result.tenant_external_id}")
    lines.append(f"organization_id: {result.organization_id}")
    lines.append(f"closed sessions checked: {result.closed_sessions_checked}")
    lines.append(f"drifted sessions before: {result.drifted_before}")
    lines.append(f"repaired: {result.applied}")
    lines.append(f"skipped (state changed): {len(result.skipped_state_changed)}")
    if result.skipped_state_changed:
        lines.append(f"skipped session_ids: {result.skipped_state_changed}")
    lines.append(f"drifted sessions after: {result.drifted_after}")
    return "\n".join(lines)


async def run_apply(
    db: AsyncSession,
    *,
    tenant_external_id: str,
    organization_id: int,
) -> ApplyResult:
    """Repair every drifted closed session through the shared lifecycle.

    Each session is re-read tenant-scoped immediately before the repair and is
    defensively re-asserted to still be ``closed``; a session whose status
    changed is skipped and reported. The repair delegates to
    ``PublicChatResolutionService.close_lifecycle`` - this function contains no
    lifecycle SQL. The lifecycle owns its own commits (a single atomic,
    tenant-scoped core transaction per session, then the SLA milestone), so the
    loop is intentionally not wrapped in one outer transaction.
    """
    snapshot = await collect_drift_snapshot(
        db,
        tenant_external_id=tenant_external_id,
        organization_id=organization_id,
    )

    applied = 0
    skipped_state_changed: list[int] = []
    when = datetime.now(UTC)

    for drifted in snapshot.drifted:
        # Defensive, tenant-scoped re-read: never mutate a session on the
        # strength of a possibly-stale snapshot.
        session = await PublicChatRepository.get_session_by_id_for_tenant(
            db,
            drifted.session_id,
            organization_id,
        )
        if session is None or session.status != CLOSED_STATUS:
            # State changed (or the row left the tenant scope) between the scan
            # and the repair. Skip it; a later run will re-evaluate it.
            skipped_state_changed.append(drifted.session_id)
            continue

        await PublicChatResolutionService.close_lifecycle(
            db,
            session=session,
            organization_id=organization_id,
            when=when,
            closed_by=REPAIR_CLOSED_BY,
        )
        applied += 1

    post = await collect_drift_snapshot(
        db,
        tenant_external_id=tenant_external_id,
        organization_id=organization_id,
    )
    log.info(
        "reconcile_public_chat_legacy_drift_apply",
        tenant_external_id=tenant_external_id,
        organization_id=organization_id,
        closed_sessions_checked=snapshot.closed_sessions_checked,
        drifted_before=len(snapshot.drifted),
        applied=applied,
        skipped_state_changed=len(skipped_state_changed),
        drifted_after=len(post.drifted),
    )
    return ApplyResult(
        tenant_external_id=tenant_external_id,
        organization_id=organization_id,
        closed_sessions_checked=snapshot.closed_sessions_checked,
        drifted_before=len(snapshot.drifted),
        applied=applied,
        skipped_state_changed=skipped_state_changed,
        drifted_after=len(post.drifted),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit (default) or repair legacy public-chat close drift for one "
            "tenant, resolved by Organization.external_id."
        )
    )
    parser.add_argument(
        "--tenant",
        required=True,
        help="tenant Organization.external_id to audit/repair (e.g. rispu).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="repair the drift through the shared close lifecycle (default: dry run).",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="report only; never mutates (default).",
    )
    args = parser.parse_args(argv)
    args.tenant = args.tenant.strip()
    if not args.tenant:
        parser.error("--tenant must be a non-empty external_id")
    return args


async def _main(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        organization_id = await resolve_organization_id(db, args.tenant)
        if organization_id is None:
            print(
                f"ERROR: no organization has external_id '{args.tenant}'; "
                "nothing to do."
            )
            return 2

        if args.apply:
            result = await run_apply(
                db,
                tenant_external_id=args.tenant,
                organization_id=organization_id,
            )
            print(render_apply_result(result))
            return 0

        snapshot = await collect_drift_snapshot(
            db,
            tenant_external_id=args.tenant,
            organization_id=organization_id,
        )
        print(render_dry_run(snapshot))
        return 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
