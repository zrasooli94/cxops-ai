#!/usr/bin/env python3
"""Read-only classification of jobs affected by the former MissingGreenlet defect.

Background (PR #3): a ``conversation.reply`` job whose delivery raised the
NON-retryable ``ConversationDeliveryError`` hit ``MissingGreenlet`` in its
failure branch. The worker then treated it as retryable: the job went to
``retry`` (or eventually ``failed``) with the MissingGreenlet text as
``last_error`` and the message ``retrying`` / ``provider_unavailable``.

Every ``ConversationDeliveryError`` is raised by validation BEFORE any provider
call, so no attempt that took that branch wrote to Zendesk. A LATER retry could
have delivered only if the blocking condition (target, conversation status,
message state) changed in between - which is what this tool looks for.

    .venv/bin/python -m scripts.diagnose_affected_jobs          # table
    .venv/bin/python -m scripts.diagnose_affected_jobs --json   # machine-readable

Safety (fail closed):

* Runs in ONE transaction that is set ``READ ONLY`` and verified with
  ``SHOW transaction_read_only`` before any query; otherwise it aborts. The
  transaction is rolled back at the end.
* Refuses a non-local database unless ``--allow-remote-read-only`` is given
  (an owner decision; e.g. the production Neon database).
* Never replays, requeues, edits, or contacts a provider.
* Prints ids, statuses, counts, and bounded categories only - never message
  bodies, payload contents (beyond ``message_id``), tokens, or credentials.

Exit codes: 0 = report produced, 1 = refused/aborted, 2 = bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

GREENLET_MARKERS = ("greenlet_spawn", "MissingGreenlet")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "db", "host.docker.internal"}

# Recovery categories (each maps to a section of the recovery decision package).
ALREADY_SENT = "already_sent__job_bookkeeping_only"
NEEDS_PROVIDER_VERIFICATION = "external_verification_required"
TERMINAL_TARGET = "terminal_nonretryable__supervised_fail"
LOCAL_REDELIVERY = "local_provider__supervised_redelivery_eligible"
LEAVE_UNTOUCHED = "leave_untouched__investigate"
ATTEMPTS_OVERFLOW = "attempts_exceeded_max__leave_untouched"


@dataclass(frozen=True)
class JobRow:
    job_id: int
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    organization_id: int | None
    error_category: str
    message_id: int | None
    message_found: bool
    delivery_status: str | None
    delivery_error_code: str | None
    provider: str | None
    conversation_status: str | None
    has_delivery_token: bool | None
    thread_matches_ticket: bool | None
    ticket_source: str | None
    ticket_external_id_numeric: bool | None
    recovery_category: str
    reason: str


def database_host_category(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host in LOCAL_HOSTS or host.endswith((".localhost", ".local")):
        return "local"
    if host.endswith(".neon.tech"):
        return "neon"
    return "remote"


def error_category(last_error: str | None) -> str:
    text = last_error or ""
    if any(marker in text for marker in GREENLET_MARKERS):
        return "missing_greenlet"
    if text.startswith("Recovered stale processing job"):
        return "stale_recovered"
    return "other" if text else "none"


def classify(row: dict[str, Any]) -> tuple[str, str]:
    """(recovery_category, reason) for one affected job. Pure; tested directly."""
    if row["attempts"] > row["max_attempts"]:
        return ATTEMPTS_OVERFLOW, "attempts exceed max_attempts (stale-recovery loop)"
    if row["job_type"] != "conversation.reply":
        return LEAVE_UNTOUCHED, "not a conversation.reply job"
    if not row["message_found"]:
        return LEAVE_UNTOUCHED, "referenced message missing or owned by another tenant"
    if row["delivery_status"] == "sent":
        return ALREADY_SENT, "message already sent; only the job record is stale"
    if not row.get("has_delivery_token"):
        # No token => no reply marker => no exactly-once guarantee. delivery
        # rejects these before any provider call; they can never be sent safely
        # by automation.
        return TERMINAL_TARGET, "message has no delivery token (no exactly-once marker)"
    provider = row["provider"]
    if provider == "cxops":
        return LOCAL_REDELIVERY, "local provider: no external system to duplicate into"
    if provider != "zendesk":
        return TERMINAL_TARGET, f"unsupported provider '{provider}'"
    if row["conversation_status"] != "open":
        return TERMINAL_TARGET, "conversation is not open"
    if row["ticket_source"] != "zendesk" or not row["ticket_external_id_numeric"]:
        return TERMINAL_TARGET, "ticket is not a valid Zendesk execution target"
    if not row.get("thread_matches_ticket"):
        return TERMINAL_TARGET, "conversation thread id does not match the ticket"
    return (
        NEEDS_PROVIDER_VERIFICATION,
        "valid Zendesk target now: verify the reply marker on Zendesk before any action",
    )


AFFECTED_SQL = """
SELECT j.id AS job_id, j.job_type, j.status, j.attempts, j.max_attempts,
       j.organization_id, j.last_error,
       CASE WHEN j.payload ? 'message_id' AND (j.payload->>'message_id') ~ '^[0-9]+$'
            THEN (j.payload->>'message_id')::bigint END AS message_id
FROM integration_jobs j
WHERE j.last_error ILIKE '%greenlet%'
   OR j.attempts > j.max_attempts
   OR (j.job_type = 'conversation.reply' AND j.status IN ('retry', 'failed', 'processing'))
ORDER BY j.id
LIMIT :limit
"""

MESSAGE_SQL = """
SELECT m.delivery_status, m.delivery_error_code, c.provider, c.status AS conversation_status,
       (m.delivery_token IS NOT NULL) AS has_delivery_token,
       (c.external_thread_id IS NOT DISTINCT FROM t.external_id) AS thread_matches_ticket,
       t.source AS ticket_source,
       (t.external_id ~ '^[0-9]+$') AS ticket_external_id_numeric
FROM conversation_messages m
JOIN conversations c ON c.id = m.conversation_id AND c.organization_id = m.organization_id
LEFT JOIN tickets t ON t.id = c.ticket_id AND t.organization_id = c.organization_id
WHERE m.id = :message_id AND m.organization_id = :organization_id
"""


async def collect(session: Any, *, limit: int) -> list[JobRow]:
    from sqlalchemy import text

    await session.execute(text("SET TRANSACTION READ ONLY"))
    read_only = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
    if read_only != "on":
        raise RuntimeError("could not enter a READ ONLY transaction; aborting")

    rows: list[JobRow] = []
    jobs = (await session.execute(text(AFFECTED_SQL), {"limit": limit})).mappings().all()
    for job in jobs:
        details: dict[str, Any] = {
            "delivery_status": None,
            "delivery_error_code": None,
            "provider": None,
            "conversation_status": None,
            "has_delivery_token": None,
            "thread_matches_ticket": None,
            "ticket_source": None,
            "ticket_external_id_numeric": None,
        }
        found = False
        if job["message_id"] is not None and job["organization_id"] is not None:
            match = (
                await session.execute(
                    text(MESSAGE_SQL),
                    {"message_id": job["message_id"], "organization_id": job["organization_id"]},
                )
            ).mappings().first()
            if match is not None:
                found = True
                details.update(dict(match))
        facts = {**dict(job), **details, "message_found": found}
        category, reason = classify(facts)
        rows.append(
            JobRow(
                job_id=job["job_id"],
                job_type=job["job_type"],
                status=job["status"],
                attempts=job["attempts"],
                max_attempts=job["max_attempts"],
                organization_id=job["organization_id"],
                error_category=error_category(job["last_error"]),
                message_id=job["message_id"],
                message_found=found,
                delivery_status=details["delivery_status"],
                delivery_error_code=details["delivery_error_code"],
                provider=details["provider"],
                conversation_status=details["conversation_status"],
                has_delivery_token=details["has_delivery_token"],
                thread_matches_ticket=details["thread_matches_ticket"],
                ticket_source=details["ticket_source"],
                ticket_external_id_numeric=details["ticket_external_id_numeric"],
                recovery_category=category,
                reason=reason,
            )
        )
    return rows


def summarize(rows: list[JobRow]) -> dict[str, Any]:
    return {
        "affected_jobs": len(rows),
        "by_job_type_and_status": dict(
            sorted(Counter(f"{r.job_type}:{r.status}" for r in rows).items())
        ),
        "by_error_category": dict(sorted(Counter(r.error_category for r in rows).items())),
        "by_recovery_category": dict(
            sorted(Counter(r.recovery_category for r in rows).items())
        ),
        "attempts_distribution": dict(sorted(Counter(r.attempts for r in rows).items())),
        "message_delivery_status": dict(
            sorted(Counter(str(r.delivery_status) for r in rows).items())
        ),
    }


def render_table(rows: list[JobRow]) -> str:
    header = "job_id | type | status | att/max | error | msg | delivery | provider | category"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.job_id} | {r.job_type} | {r.status} | {r.attempts}/{r.max_attempts} | "
            f"{r.error_category} | {r.message_id if r.message_found else '-'} | "
            f"{r.delivery_status or '-'} | {r.provider or '-'} | {r.recovery_category}"
        )
    return "\n".join(lines)


async def _run(limit: int) -> list[JobRow]:
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            return await collect(session, limit=limit)
        finally:
            await session.rollback()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only affected-job classification.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument(
        "--allow-remote-read-only",
        action="store_true",
        help="permit a non-local database (still strictly read-only); owner decision",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 10000:
        parser.error("--limit must be between 1 and 10000")

    from app.core.config import settings

    category = database_host_category(settings.database_url)
    if category != "local" and not args.allow_remote_read_only:
        print(
            f"refusing: DATABASE_URL is {category}, not local. Re-run with "
            "--allow-remote-read-only only with owner approval.",
            file=sys.stderr,
        )
        return 1

    try:
        rows = asyncio.run(_run(args.limit))
    except RuntimeError as exc:
        print(f"aborted: {exc}", file=sys.stderr)
        return 1

    print(render_output(rows, database_category=category, as_json=args.json))
    return 0


def render_output(rows: list[JobRow], *, database_category: str, as_json: bool) -> str:
    """The complete report text. Only ids, statuses, counts, and categories."""
    summary = {"database_category": database_category, **summarize(rows)}
    if as_json:
        return json.dumps({"summary": summary, "jobs": [asdict(r) for r in rows]}, indent=2)
    return json.dumps(summary, indent=2) + "\n\n" + render_table(rows)


if __name__ == "__main__":
    raise SystemExit(main())
