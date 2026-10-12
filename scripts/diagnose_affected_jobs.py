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

Consistency: the whole report is read from ONE ``REPEATABLE READ`` snapshot,
so a job and its message can never be observed at different moments while
workers or staff keep changing them.

Decision layer (Phase 1P.8R): every row also gets a ``recommended_action``,
the ``approval_required`` before anyone may act on it, the message age, and
``auto_executes_after_deploy`` - whether the next worker run would pick the
job up by itself once the fixes are deployed. ``--operator-report`` prints a
plain-language summary grouped by action.

Exit codes: 0 = complete report, 3 = report produced but INCOMPLETE (more
affected rows than ``--limit``; do not act on it), 1 = refused/aborted,
2 = bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
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
MANUAL_MARKER_VERIFICATION = "delivery_unverified__operator_verifies_marker"

# Recommended actions (the supervised decision workflow).
ACTION_LEAVE = "leave_untouched"
ACTION_RECONCILE_COMPLETED = "reconcile_job_record_only"
ACTION_SUPERVISED_FAIL = "supervised_fail_no_send"
ACTION_LOCAL_REDELIVER = "supervised_local_redelivery"
ACTION_VERIFY_THEN_DECIDE = "verify_marker_then_decide"
ACTION_LATE_REPLY = "late_reply_human_decision"
ACTION_OPERATOR_VERIFY = "operator_verifies_marker_on_zendesk"

# A customer reply that has waited this long is no longer "just a retry": a
# human decides whether it should still go out (it may be stale or harmful).
LATE_REPLY_HOURS = 24

APPROVALS = {
    ACTION_LEAVE: "none (no change)",
    ACTION_RECONCILE_COMPLETED: "owner: bookkeeping-only job update; no provider call",
    ACTION_SUPERVISED_FAIL: "owner: mark job and message failed by id; no provider call",
    ACTION_LOCAL_REDELIVER: "owner: local (cxops) redelivery; no external provider",
    ACTION_VERIFY_THEN_DECIDE: (
        "operator checks the Zendesk ticket for the reply marker; owner approves "
        "reconcile-as-sent (marker present) or a single supervised send (absent)"
    ),
    ACTION_LATE_REPLY: (
        "operator checks the marker first; if absent, the owner decides whether a "
        "late reply should be sent at all or cancelled"
    ),
    ACTION_OPERATOR_VERIFY: (
        "operator checks the Zendesk ticket for the reply marker; owner approves "
        "reconcile-as-sent or a manual retry"
    ),
}


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
    message_age_hours: float | None = None
    recommended_action: str = ACTION_LEAVE
    approval_required: str = APPROVALS[ACTION_LEAVE]
    auto_executes_after_deploy: bool = False


@dataclass
class DiagnosticReport:
    rows: list[JobRow] = field(default_factory=list)
    truncated: bool = False
    snapshot_at: str | None = None


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
    if row.get("delivery_error_code") == "delivery_unverified":
        return (
            MANUAL_MARKER_VERIFICATION,
            "the marker scan could not prove whether the reply was delivered",
        )
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


def decide(category: str, *, status: str, age_hours: float | None) -> tuple[str, bool]:
    """(recommended_action, auto_executes_after_deploy). Pure; tested directly.

    ``auto_executes_after_deploy``: the job is ``pending``/``retry`` (or a
    ``processing`` row stale recovery will re-queue), so the next deployed
    worker run executes it WITHOUT a human - the reason to decide first.
    """
    auto = status in ("pending", "retry", "processing")
    if category in (ATTEMPTS_OVERFLOW, LEAVE_UNTOUCHED):
        return ACTION_LEAVE, auto
    if category == ALREADY_SENT:
        return ACTION_RECONCILE_COMPLETED, auto
    if category == TERMINAL_TARGET:
        return ACTION_SUPERVISED_FAIL, auto
    if category == LOCAL_REDELIVERY:
        return ACTION_LOCAL_REDELIVER, auto
    if category == MANUAL_MARKER_VERIFICATION:
        return ACTION_OPERATOR_VERIFY, auto
    if age_hours is not None and age_hours >= LATE_REPLY_HOURS:
        return ACTION_LATE_REPLY, auto
    return ACTION_VERIFY_THEN_DECIDE, auto


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
LIMIT :limit_plus_one
"""

MESSAGE_SQL = """
SELECT m.delivery_status, m.delivery_error_code, m.created_at AS message_created_at,
       c.provider, c.status AS conversation_status,
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
    return (await collect_report(session, limit=limit)).rows


async def _fetch_message(session: Any, *, message_id: int, organization_id: int) -> Any:
    from sqlalchemy import text

    return (
        await session.execute(
            text(MESSAGE_SQL), {"message_id": message_id, "organization_id": organization_id}
        )
    ).mappings().first()


async def collect_report(
    session: Any, *, limit: int, now: datetime | None = None
) -> DiagnosticReport:
    from sqlalchemy import text

    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    read_only = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
    isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
    if read_only != "on" or isolation != "repeatable read":
        raise RuntimeError("could not enter a READ ONLY, REPEATABLE READ transaction; aborting")
    snapshot_at = (await session.execute(text("SELECT now()"))).scalar_one()
    moment = now or snapshot_at

    report = DiagnosticReport(snapshot_at=str(snapshot_at))
    rows = report.rows
    jobs = (
        await session.execute(text(AFFECTED_SQL), {"limit_plus_one": limit + 1})
    ).mappings().all()
    if len(jobs) > limit:
        report.truncated = True
        jobs = jobs[:limit]
    for job in jobs:
        details: dict[str, Any] = {
            "delivery_status": None,
            "delivery_error_code": None,
            "message_created_at": None,
            "provider": None,
            "conversation_status": None,
            "has_delivery_token": None,
            "thread_matches_ticket": None,
            "ticket_source": None,
            "ticket_external_id_numeric": None,
        }
        found = False
        if job["message_id"] is not None and job["organization_id"] is not None:
            match = await _fetch_message(
                session,
                message_id=job["message_id"],
                organization_id=job["organization_id"],
            )
            if match is not None:
                found = True
                details.update(dict(match))
        facts = {**dict(job), **details, "message_found": found}
        category, reason = classify(facts)
        created = details["message_created_at"]
        age_hours = (
            round((moment - created).total_seconds() / 3600, 1) if created is not None else None
        )
        action, auto = decide(category, status=job["status"], age_hours=age_hours)
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
                message_age_hours=age_hours,
                recommended_action=action,
                approval_required=APPROVALS[action],
                auto_executes_after_deploy=auto,
            )
        )
    return report


def summarize(rows: list[JobRow]) -> dict[str, Any]:
    return {
        "affected_jobs": len(rows),
        "by_recommended_action": dict(
            sorted(Counter(r.recommended_action for r in rows).items())
        ),
        "auto_execute_after_deploy": sum(r.auto_executes_after_deploy for r in rows),
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


async def _run(limit: int) -> DiagnosticReport:
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            return await collect_report(session, limit=limit)
        finally:
            await session.rollback()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only affected-job classification.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--operator-report",
        action="store_true",
        help="plain-language summary grouped by recommended action",
    )
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
        report = asyncio.run(_run(args.limit))
    except RuntimeError as exc:
        print(f"aborted: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        # Driver errors can embed host/user details: print the type only.
        print(f"aborted: {type(exc).__name__} (details withheld)", file=sys.stderr)
        return 1

    if args.operator_report:
        print(render_operator_report(report, database_category=category))
    else:
        print(
            render_output(
                report.rows,
                database_category=category,
                as_json=args.json,
                truncated=report.truncated,
            )
        )
    return 3 if report.truncated else 0


def render_output(
    rows: list[JobRow], *, database_category: str, as_json: bool, truncated: bool = False
) -> str:
    """The complete report text. Only ids, statuses, counts, and categories."""
    summary = {
        "database_category": database_category,
        "complete": not truncated,
        **summarize(rows),
    }
    if as_json:
        return json.dumps({"summary": summary, "jobs": [asdict(r) for r in rows]}, indent=2)
    return json.dumps(summary, indent=2) + "\n\n" + render_table(rows)


def render_operator_report(report: DiagnosticReport, *, database_category: str) -> str:
    """Plain-language decision summary for the owner/operator."""
    rows = report.rows
    lines = [
        "AFFECTED-JOB RECOVERY DECISION SUMMARY (read-only; nothing was changed)",
        f"database: {database_category}   snapshot: {report.snapshot_at}",
    ]
    if report.truncated:
        lines.append(
            "!! INCOMPLETE: more affected jobs exist than --limit. Do not act on this "
            "report; re-run with a higher --limit."
        )
    auto = [r.job_id for r in rows if r.auto_executes_after_deploy]
    if auto:
        lines.append(
            f"!! {len(auto)} job(s) would be executed automatically by the next worker "
            "run once the fixes are deployed. Decide on them BEFORE deploying."
        )
    lines.append(f"affected jobs: {len(rows)}")
    by_action: dict[str, list[JobRow]] = {}
    for row in rows:
        by_action.setdefault(row.recommended_action, []).append(row)
    for action in sorted(by_action):
        group = by_action[action]
        ids = ", ".join(str(r.job_id) for r in group[:50])
        more = f" (+{len(group) - 50} more)" if len(group) > 50 else ""
        lines.append("")
        lines.append(f"[{action}] {len(group)} job(s): {ids}{more}")
        lines.append(f"  approval: {APPROVALS[action]}")
        reasons = sorted({r.reason for r in group})
        lines.append(f"  why: {'; '.join(reasons)}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
