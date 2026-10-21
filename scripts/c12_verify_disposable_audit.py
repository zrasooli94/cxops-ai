"""C12: independently verify the browser recorded ONLY fictional PG audit data.

Runs after real Chrome/Next/FastAPI e2e, under exact C2 loopback DB guard.
No real provider, staff or customer data, and never emits source/evidence
or reviewer identifiers to logs.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from scripts.chatbot_c2_disposable_guard import require_c2_disposable_environment

require_c2_disposable_environment(os.environ)

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.claim_review import (
    ClaimReviewAssignment,
    ClaimReviewBatch,
    ClaimReviewEvent,
    ClaimReviewItem,
)


async def verify(org_a: int, org_b: int) -> None:
    async with AsyncSessionLocal() as db:
        batches = (
            await db.execute(
                select(ClaimReviewBatch).where(
                    ClaimReviewBatch.organization_id == org_a
                ).order_by(ClaimReviewBatch.id)
            )
        ).scalars().all()
        assert len(batches) == 1, "one fictional browser-created batch required"
        batch = batches[0]
        assert batch.organization_id == org_a
        assert batch.dataset_id == "c6-fictional-faq-20261021"
        assert len(batch.dataset_digest) == 64

        async def count(model, org):
            return (
                await db.scalar(
                    select(func.count()).select_from(model).where(
                        model.organization_id == org
                    )
                )
            )

        assert await count(ClaimReviewItem, org_a) == 12
        assert await count(ClaimReviewAssignment, org_a) == 2
        assert await count(ClaimReviewEvent, org_a) == 3
        for model in (
            ClaimReviewBatch, ClaimReviewItem,
            ClaimReviewAssignment, ClaimReviewEvent,
        ):
            assert await count(model, org_b) == 0

        events = (
            await db.execute(
                select(ClaimReviewEvent).where(
                    ClaimReviewEvent.organization_id == org_a,
                ).order_by(ClaimReviewEvent.id)
            )
        ).scalars().all()
        assert [event.event_kind for event in events] == [
            "review", "review", "adjudication",
        ]
        assert [event.label for event in events] == [
            "contradicted", "unsupported", "contradicted",
        ]
        assert len({event.actor_subject for event in events}) == 3
        assert all(
            (event.evidence_digest and len(event.evidence_digest) == 64)
            or (event.rationale_digest and len(event.rationale_digest) == 64)
            for event in events
        )
        assert all(
            "Aster bicycle repairs are not free."
            not in str(event.__dict__)
            for event in events
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("C12 audit requires local fixture path")
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    assert data["synthetic"] is True
    asyncio.run(verify(data["org_a"], data["org_b"]))
    print("C12 real-browser-to-Postgres fictional audit VERIFIED; no real data.")
