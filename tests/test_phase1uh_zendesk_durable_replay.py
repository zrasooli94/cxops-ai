"""Phase 1U-H: durable replay uniqueness with disposable PostgreSQL.

Uses the project's configured ephemeral test database, synthetic tenant keys,
and no provider network traffic. Each test cleans its own inserted job rows.
"""
import uuid

import pytest
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.services.integration_job_service import IntegrationJobService


@pytest.mark.asyncio
async def test_signed_delivery_replay_only_enqueues_one_durable_job():
    label = uuid.uuid4().hex
    fingerprint = ("a" + label).ljust(64, "0")
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"replay-test-{label}")
        db.add(org)
        await db.commit()
        await db.refresh(org)
        organization_id = org.id
        try:
            one = await IntegrationJobService.enqueue_zendesk_event(
                db=db, invocation_id=f"first-{label}",
                signed_delivery_fingerprint=fingerprint,
                event_type="ticket.created", zendesk_ticket_id=123,
                payload={"type": "synthetic"}, organization_id=organization_id,
            )
            two = await IntegrationJobService.enqueue_zendesk_event(
                db=db, invocation_id=f"relabelled-{label}",
                signed_delivery_fingerprint=fingerprint,
                event_type="ticket.created", zendesk_ticket_id=123,
                payload={"type": "synthetic"}, organization_id=organization_id,
            )
            assert one.duplicate is False
            assert two.duplicate is True
            assert one.job_id == two.job_id
        finally:
            await db.rollback()
            await db.execute(delete(IntegrationJob).where(
                IntegrationJob.organization_id == organization_id))
            await db.delete(org)
            await db.commit()


@pytest.mark.asyncio
async def test_distinct_signatures_do_not_suppress_legitimate_deliveries():
    label = uuid.uuid4().hex
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"distinct-test-{label}")
        db.add(org)
        await db.commit()
        await db.refresh(org)
        organization_id = org.id
        try:
            results = []
            for suffix in ("a", "b"):
                result = await IntegrationJobService.enqueue_zendesk_event(
                    db=db, invocation_id=f"{suffix}-{label}",
                    signed_delivery_fingerprint=(suffix + label).ljust(64, "0"),
                    event_type="ticket.created", zendesk_ticket_id=123,
                    payload={"type": "synthetic"}, organization_id=organization_id,
                )
                results.append(result)
            assert not results[0].duplicate and not results[1].duplicate
            assert results[0].job_id != results[1].job_id
        finally:
            await db.rollback()
            await db.execute(delete(IntegrationJob).where(
                IntegrationJob.organization_id == organization_id))
            await db.delete(org)
            await db.commit()
