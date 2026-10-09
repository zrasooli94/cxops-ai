#!/usr/bin/env python3
"""Read-only live-pilot health check for the public-chat surface (Phase 1P.7).

Reuses the exact aggregation the control-center dashboard shows
(``PublicChatSummaryService``) and prints a bounded, operator-facing summary:

    .venv/bin/python -m scripts.check_public_chat_pilot --tenant rispu

Safety properties:

* **Read-only.** It runs the same SELECT-only aggregates the summary endpoint
  serves; it never writes, commits, or produces a transaction.
* **Tenant by slug.** The tenant is resolved by ``Organization.external_id``
  (the onboarding manifest slug) — never from a client value and never by
  guessing.
* **No secrets in output.** DATABASE_URL, widget keys, session tokens, and
  customer message text are never printed. The problem rows behind a warning
  (e.g. RAG failures) are only counted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.organization import Organization
from app.services.public_chat_summary_service import (
    WINDOW_TO_HOURS,
    PublicChatSummaryService,
)


def _fmt(hours: int) -> str:
    return {24: "24h", 168: "7d"}.get(hours, f"{hours}h")


def _build_report(summary: dict[str, Any], window: str) -> list[str]:
    config = summary["config"]
    queue = summary["queue"]
    queue_health = summary["queue_health"]
    window_summary = summary["window_summary"]
    rag = summary["rag"]
    safety = summary["safety"]

    lines: list[str] = []
    lines.append(
        f"  widget enabled:        {config['widget_enabled']!s:<5} "
        f"grounded replies: {config['grounded_auto_reply_enabled']!s}"
    )
    lines.append(
        f"  queue ({window}):          waiting={queue['human_requested']} "
        f"assigned={queue['human_assigned']} active_total={queue['active_total']} "
        f"health={queue_health['health']}"
    )
    oldest = queue_health["oldest_waiting_minutes"]
    if oldest is None:
        lines.append("  oldest waiting:         none")
    else:
        lines.append(f"  oldest waiting:         {oldest} min")
    lines.append(
        f"  activity ({window}):      sessions={window_summary['sessions_created']} "
        f"messages={window_summary['customer_messages']} "
        f"closed={window_summary['sessions_closed']} "
        f"grounded={window_summary['grounded_public_auto_replies']}"
    )
    lines.append(
        f"  rag ({window}):          requests={rag['rag_requests']} "
        f"grounded={rag['rag_grounded']} errors={rag['rag_errors']} "
        f"avg_latency_ms={rag['avg_rag_latency_ms']:.0f}"
    )
    lines.append(
        f"  safety ({window}):       integration_jobs={safety['public_chat_integration_jobs']} "
        f"autonomous_executions={safety['autonomous_public_chat_executions']}"
    )
    return lines


def _warnings(summary: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not summary["config"]["widget_enabled"]:
        warnings.append("widget is disabled")
    if summary["queue_health"]["health"] == "attention":
        warnings.append(
            f"human queue has been waiting >= 15 minutes "
            f"(oldest {summary['queue_health']['oldest_waiting_minutes']} min)"
        )
    if summary["rag"]["rag_errors"]:
        warnings.append(f"{summary['rag']['rag_errors']} public-chat RAG failure(s) in window")
    if summary["safety"]["public_chat_integration_jobs"]:
        warnings.append(
            f"{summary['safety']['public_chat_integration_jobs']} public-chat integration "
            "job(s) in window — review operator dashboards"
        )
    if summary["safety"]["autonomous_public_chat_executions"]:
        warnings.append(
            f"{summary['safety']['autonomous_public_chat_executions']} autonomous public-chat "
            "execution(s) in window — confirm they were expected"
        )
    return warnings


async def _run(args: argparse.Namespace) -> int:
    lines: list[str] = []
    code = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Organization).where(Organization.external_id == args.tenant)
        )
        organization = result.scalar_one_or_none()
        if organization is None:
            print(
                f"error: no tenant with external_id={args.tenant!r} "
                "(the onboarding manifest slug).",
                file=sys.stderr,
            )
            return 2

        summary = await PublicChatSummaryService.summary(
            db,
            organization_id=organization.id,
            window_hours=WINDOW_TO_HOURS[args.window],
        )

    lines.append(f"tenant: {args.tenant} (organization_id={organization.id})")
    lines.extend(_build_report(summary, args.window))

    warnings = _warnings(summary)
    for warning in warnings:
        lines.append(f"  WARNING: {warning}")
    if warnings:
        lines.append("verdict: ATTENTION")
        code = 1
    else:
        lines.append("verdict: READY")

    print("CXOps public-chat live-pilot check")
    print()
    for line in lines:
        print(line)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant",
        default="rispu",
        help="The onboarding tenant slug (Organization.external_id). Default: rispu",
    )
    parser.add_argument(
        "--window",
        choices=("24h", "7d"),
        default="24h",
        help="Look-back window for activity, RAG, and safety aggregates. Default: 24h",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())