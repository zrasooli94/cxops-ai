"""Phase 1U-H: simultaneous enqueue collisions on disposable Postgres only."""
import asyncio
import uuid

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.services.integration_job_service import IntegrationJobService


@pytest.mark.asyncio
async def test_concurrent_relabelled_webhooks_create_exactly_one_job():
    token = uuid.uuid4().hex
    fingerprint = (token + "0" * 64)[:64]
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"replay-race-{token}")
        db.add(org)
        await db.commit()
        await db.refresh(org)
        organization_id = org.id

    async def deliver(n: int):
        async with AsyncSessionLocal() as session:
            return await IntegrationJobService.enqueue_zendesk_event(
                db=session, invocation_id=f"race-{token}-{n}",
                signed_delivery_fingerprint=fingerprint,
                event_type="ticket.created", zendesk_ticket_id=813,
                payload={"type": "synthetic"}, organization_id=organization_id,
            )

    try:
        a, b = await asyncio.gather(deliver(1), deliver(2))
        assert a.job_id == b.job_id
        assert sorted([a.duplicate, b.duplicate]) == [False, True]
        async with AsyncSessionLocal() as session:
            jobs = (await session.execute(
                select(IntegrationJob).where(
                    IntegrationJob.organization_id == organization_id,
                    IntegrationJob.signed_delivery_fingerprint == fingerprint,
                )
            )).scalars().all()
            assert len(jobs) == 1
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(IntegrationJob).where(
                IntegrationJob.organization_id == organization_id))
            await session.execute(delete(Organization).where(
                Organization.id == organization_id))
            await session.commit()
