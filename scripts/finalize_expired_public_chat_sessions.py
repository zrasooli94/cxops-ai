"""Finalize expired live public-chat sessions (Phase 1P.7.3A).

A public-chat session can pass its ``expires_at`` while still in a live status
(``ai_active`` / ``human_requested`` / ``human_assigned``). The customer
credential is already invalid at that point, so the session is effectively dead,
but the database lifecycle was never finalized: the linked ticket is still open
and the linked conversation is still open.

This tool finalizes exactly those sessions for ONE tenant, using the reusable
``PublicChatExpiryService`` - which in turn delegates to the EXISTING shared
close lifecycle ``PublicChatResolutionService.close_lifecycle``. It contains no
lifecycle SQL of its own.

It is DRY-RUN by default and only ever mutates when ``--apply`` is supplied. The
tenant is resolved from ``Organization.external_id`` - never from a numeric id
the operator types by hand - so the report scope is unambiguous.

Stage A is manual only: there is NO worker integration yet, NO migration, NO
destructive SQL, NO deletes, and NO AI/RAG/tool execution.

Safety properties:
  - read-only unless ``--apply`` is passed
  - candidates are ONLY live sessions past ``expires_at``; an already-closed
    session can never be a candidate and is never mutated
  - never prints session tokens, token hashes, widget keys or their hashes,
    customer message text, email, IP, secrets, or DATABASE_URL - bounded
    identifiers and counts only
  - race-safe: each candidate is re-read under ``SELECT ... FOR UPDATE`` and
    re-checked for live status + expiry before it is finalized; a candidate that
    changed is skipped, never mutated
  - idempotent: a second ``--apply`` finds zero candidates and emits no second
    close metric/log

Usage:
    python -m scripts.finalize_expired_public_chat_sessions --tenant rispu
    python -m scripts.finalize_expired_public_chat_sessions --tenant rispu --apply
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.organization import Organization
from app.services.public_chat_expiry_service import (
    ExpiryFinalizeResult,
    ExpirySnapshot,
    PublicChatExpiryService,
)

DRY_RUN_FOOTER = "DRY RUN — no changes made"


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


def render_dry_run(*, tenant_external_id: str, snapshot: ExpirySnapshot) -> str:
    """Human-safe dry-run report: bounded ids/statuses and counts only."""
    lines: list[str] = []
    lines.append("PUBLIC-CHAT EXPIRED SESSION FINALIZATION — DRY RUN")
    lines.append("=" * 60)
    lines.append(f"tenant (external_id): {tenant_external_id}")
    lines.append(f"organization_id: {snapshot.organization_id}")
    lines.append(f"expired live sessions checked: {snapshot.expired_sessions_checked}")
    lines.append(f"expired candidates: {len(snapshot.candidates)}")
    if snapshot.candidates:
        lines.append("")
        lines.append("expired live sessions (bounded identifiers only):")
        lines.append(
            "  session_id | ticket_id | conversation_id | session_status | "
            "ticket_status | conversation_status | expires_at"
        )
        for candidate in snapshot.candidates:
            lines.append(
                f"  {candidate.session_id} | {candidate.ticket_id} | "
                f"{candidate.conversation_id} | {candidate.session_status} | "
                f"{candidate.ticket_status} | "
                f"{candidate.conversation_status} | "
                f"{candidate.expires_at.isoformat()}"
            )
    lines.append("")
    lines.append(DRY_RUN_FOOTER)
    return "\n".join(lines)


def render_apply_result(
    *,
    tenant_external_id: str,
    result: ExpiryFinalizeResult,
) -> str:
    """Human-safe apply report: bounded ids and counts only."""
    lines: list[str] = []
    lines.append("PUBLIC-CHAT EXPIRED SESSION FINALIZATION — APPLY COMPLETE")
    lines.append("=" * 60)
    lines.append(f"tenant (external_id): {tenant_external_id}")
    lines.append(f"organization_id: {result.organization_id}")
    lines.append(f"expired candidates before: {result.candidates_before}")
    lines.append(f"finalized: {result.finalized}")
    lines.append(f"skipped (state/expiry changed): {len(result.skipped)}")
    if result.skipped:
        lines.append(f"skipped session_ids: {result.skipped}")
    lines.append(f"expired candidates after: {result.candidates_after}")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Finalize (default: audit) expired live public-chat sessions for one "
            "tenant, resolved by Organization.external_id."
        )
    )
    parser.add_argument(
        "--tenant",
        required=True,
        help="tenant Organization.external_id to audit/finalize (e.g. rispu).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="finalize expired sessions through the shared close lifecycle "
        "(default: dry run).",
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
            result = await PublicChatExpiryService.finalize_expired_sessions(
                db,
                organization_id=organization_id,
            )
            print(render_apply_result(tenant_external_id=args.tenant, result=result))
            return 0

        snapshot = await PublicChatExpiryService.collect_expired_sessions(
            db,
            organization_id=organization_id,
        )
        print(render_dry_run(tenant_external_id=args.tenant, snapshot=snapshot))
        return 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
