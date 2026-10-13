"""Re-queue public-chat turns stranded by a code rollback (Phase 1P.9R).

Why this exists: a release older than Phase 1P.9 does not know the
``public_chat.turn`` job type. If the code is rolled back while turns are still
queued (the documented procedure is: kill switch -> wait for zero open turns ->
roll back), the old scheduled worker claims those jobs and fails them
permanently with ``Unknown job type: public_chat.turn`` - the customer message
is stored but never answered and never handed to a human (verified against
master b50e741 in the Phase 1P.9R cross-version tests).

After rolling FORWARD again, this tool finds exactly those jobs and returns
them to ``pending`` so the API's recovery picks them up. A turn is only
re-queued when ALL hold:

* job type ``public_chat.turn``, status ``failed``, and its last error is the
  unknown-job-type failure (nothing else is touched);
* no reply exists for that message (reply de-duplication key);
* its session is still open (``ai_active``) - a handed-off or closed session
  is reported, not re-queued.

It is DRY-RUN by default and only writes with ``--apply``; each write is a
fenced ``UPDATE ... WHERE status = 'failed'`` so it is idempotent. Output is
ids and counts only - never message text, tokens or customer data.

    python -m scripts.repair_rolled_back_chat_turns                 # report
    python -m scripts.repair_rolled_back_chat_turns --apply         # re-queue
    python -m scripts.repair_rolled_back_chat_turns --organization-id 7

Running ``--apply`` against production is a production data change and needs
owner approval.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

ROLLBACK_ERROR_PREFIX = "Unknown job type: public_chat.turn"


@dataclass
class RepairReport:
    examined: int = 0
    requeued: list[int] = field(default_factory=list)
    already_replied: list[int] = field(default_factory=list)
    session_not_open: list[int] = field(default_factory=list)
    unreadable: list[int] = field(default_factory=list)

    def render(self, *, applied: bool) -> str:
        verb = "re-queued" if applied else "would re-queue"
        return "\n".join(
            [
                f"rolled-back public_chat.turn jobs examined: {self.examined}",
                f"{verb}: {len(self.requeued)} {self.requeued[:50]}",
                f"skipped, reply already exists: {len(self.already_replied)}",
                (
                    "skipped, session not open (handed off/closed): "
                    f"{len(self.session_not_open)} {self.session_not_open[:50]}"
                ),
                f"skipped, payload unreadable: {len(self.unreadable)}",
                "" if applied else "dry run: nothing was written (use --apply)",
            ]
        ).strip()


async def repair(
    db: AsyncSession,
    *,
    apply: bool,
    organization_id: int | None = None,
    limit: int = 500,
) -> RepairReport:
    from app.models.conversation_message import ConversationMessage
    from app.models.integration_job import IntegrationJob
    from app.models.public_chat import PublicChatSession
    from app.services.public_chat_service import (
        DEDUPE_REPLY_PREFIX,
        PUBLIC_CHAT_TURN_JOB_TYPE,
    )

    query = (
        select(IntegrationJob)
        .where(
            IntegrationJob.job_type == PUBLIC_CHAT_TURN_JOB_TYPE,
            IntegrationJob.status == "failed",
            IntegrationJob.last_error.like(f"{ROLLBACK_ERROR_PREFIX}%"),
        )
        .order_by(IntegrationJob.id)
        .limit(limit)
    )
    if organization_id is not None:
        query = query.where(IntegrationJob.organization_id == organization_id)
    jobs = list((await db.execute(query)).scalars())
    report = RepairReport(examined=len(jobs))
    for job in jobs:
        payload = job.payload or {}
        try:
            session_id = int(payload["session_id"])
            client_message_id = str(payload["client_message_id"])
        except (KeyError, TypeError, ValueError):
            report.unreadable.append(job.id)
            continue
        session = (
            await db.execute(
                select(PublicChatSession).where(
                    PublicChatSession.id == session_id,
                    PublicChatSession.organization_id == job.organization_id,
                )
            )
        ).scalar_one_or_none()
        if session is None or session.status != "ai_active":
            report.session_not_open.append(job.id)
            continue
        replied = (
            await db.execute(
                select(ConversationMessage.id).where(
                    ConversationMessage.organization_id == job.organization_id,
                    ConversationMessage.conversation_id == session.conversation_id,
                    ConversationMessage.dedupe_key == f"{DEDUPE_REPLY_PREFIX}{client_message_id}",
                )
            )
        ).first()
        if replied is not None:
            report.already_replied.append(job.id)
            continue
        if apply:
            result = await db.execute(
                update(IntegrationJob)
                .where(IntegrationJob.id == job.id, IntegrationJob.status == "failed")
                .values(
                    status="pending",
                    attempts=0,
                    last_error=None,
                    locked_at=None,
                    available_at=datetime.now(UTC),
                )
            )
            if not result.rowcount:  # type: ignore[attr-defined]
                continue
        report.requeued.append(job.id)
    if apply:
        await db.commit()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="re-queue (default: dry run)")
    parser.add_argument("--organization-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=500)
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    from app.core.database import AsyncSessionLocal, engine

    try:
        async with AsyncSessionLocal() as db:
            report = await repair(
                db, apply=args.apply, organization_id=args.organization_id, limit=args.limit
            )
    finally:
        await engine.dispose()
    print(report.render(applied=args.apply))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
