"""Phase 1U-H independent security review: adversarial regression suite.

Synthetic tenants, secrets and payloads only; disposable PostgreSQL; no real
Zendesk call. Drives the real HTTP route (``/webhooks/zendesk/tickets/{id}``)
so signature verification, header handling, fingerprinting and the durable
unique indexes are all exercised together.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.integration_job import IntegrationJob
from tests import test_zendesk_tenant as base

_configure_env = base._configure_env  # autouse: strict-ciphertext keys, auth config
make_client = base.make_client
zendesk_env = base.zendesk_env
db = base.db


def _headers(signed: dict, invocation: str | None, **overrides) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Zendesk-Webhook-Signature": signed["signature"],
        "X-Zendesk-Webhook-Signature-Timestamp": signed["timestamp"],
    }
    if invocation is not None:
        headers["X-Zendesk-Webhook-Invocation-Id"] = invocation
    headers.update(overrides)
    return headers


def _payload(ticket_id: int, event_id: str | None = None) -> dict:
    body = {
        "type": "zen:event-type:ticket.created",
        "detail": {"id": ticket_id},
        "id": event_id or uuid.uuid4().hex,
    }
    return body


def _wire(headers: dict) -> dict:
    """Send non-ASCII header text as raw latin-1 bytes, as a hostile client can."""
    out = {}
    for key, value in headers.items():
        if isinstance(value, str) and not value.isascii():
            value = value.encode("latin-1", errors="replace")
        out[key] = value
    return out


async def _post(client, integration: str, content: bytes, headers: dict):
    headers = _wire(headers)
    return await client.post(
        f"/webhooks/zendesk/tickets/{integration}", content=content, headers=headers
    )


async def _jobs_for(org_id: int) -> list[IntegrationJob]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(IntegrationJob).where(
                IntegrationJob.organization_id == org_id,
                IntegrationJob.job_type == "zendesk.ticket_event",
            )
        )
        return list(rows.scalars().all())


# --- exact authenticated bytes, relabelling, unsigned header --------------------


@pytest.mark.asyncio
async def test_relabelled_invocation_of_one_signed_delivery_is_one_job(make_client, zendesk_env):
    signed = base._sign(zendesk_env["secret_a"], _payload(910_001))
    async with make_client() as client:
        results = [
            await _post(client, zendesk_env["integration_a"], signed["content"],
                        _headers(signed, f"relabel-{i}-{uuid.uuid4().hex}"))
            for i in range(5)
        ]
    assert [r.status_code for r in results] == [202] * 5
    assert len({r.json()["job_id"] for r in results}) == 1
    assert [r.json()["duplicate"] for r in results] == [False, True, True, True, True]
    assert len(await _jobs_for(zendesk_env["org_a_id"])) == 1


@pytest.mark.asyncio
async def test_a_changed_byte_or_timestamp_is_a_different_delivery(make_client, zendesk_env):
    """Not a general event de-duplication: re-signed deliveries are new jobs."""
    event_id = uuid.uuid4().hex
    first = base._sign(zendesk_env["secret_a"], _payload(910_002, event_id))
    second = base._sign(
        zendesk_env["secret_a"], _payload(910_002, event_id),
        when=datetime.now(UTC) + timedelta(seconds=1),
    )
    assert first["timestamp"] != second["timestamp"]
    async with make_client() as client:
        r1 = await _post(client, zendesk_env["integration_a"], first["content"],
                         _headers(first, f"re-{uuid.uuid4().hex}"))
        r2 = await _post(client, zendesk_env["integration_a"], second["content"],
                         _headers(second, f"re-{uuid.uuid4().hex}"))
    assert r1.json()["job_id"] != r2.json()["job_id"]
    assert r2.json()["duplicate"] is False  # documented limitation (see review)


@pytest.mark.asyncio
async def test_a_signed_delivery_replayed_to_another_tenant_is_rejected(make_client, zendesk_env):
    signed = base._sign(zendesk_env["secret_a"], _payload(910_003))
    async with make_client() as client:
        ok = await _post(client, zendesk_env["integration_a"], signed["content"],
                         _headers(signed, f"x-{uuid.uuid4().hex}"))
        cross = await _post(client, zendesk_env["integration_b"], signed["content"],
                            _headers(signed, f"y-{uuid.uuid4().hex}"))
    assert ok.status_code == 202
    assert cross.status_code == 401
    assert await _jobs_for(zendesk_env["org_b_id"]) == []


# --- cross-tenant invocation-id collisions must never 500 or leak ---------------


@pytest.mark.asyncio
async def test_another_tenants_invocation_id_collision_is_isolated(make_client, zendesk_env):
    shared = f"shared-inv-{uuid.uuid4().hex}"
    a = base._sign(zendesk_env["secret_a"], _payload(910_004))
    b = base._sign(zendesk_env["secret_b"], _payload(910_005))
    async with make_client() as client:
        ra = await _post(client, zendesk_env["integration_a"], a["content"], _headers(a, shared))
        rb = await _post(client, zendesk_env["integration_b"], b["content"], _headers(b, shared))
    assert ra.status_code == 202 and ra.json()["duplicate"] is False
    # B must get its OWN job; never a 500, never A's job id or status.
    assert rb.status_code == 202, rb.text
    assert rb.json()["duplicate"] is False
    assert rb.json()["job_id"] != ra.json()["job_id"]
    a_jobs = await _jobs_for(zendesk_env["org_a_id"])
    b_jobs = await _jobs_for(zendesk_env["org_b_id"])
    assert [j.id for j in a_jobs] == [ra.json()["job_id"]]
    assert [j.id for j in b_jobs] == [rb.json()["job_id"]]


@pytest.mark.asyncio
async def test_legacy_unscoped_job_of_another_tenant_never_suppresses_a_new_event(
    make_client, zendesk_env
):
    """Jobs written by the previous revision used the raw invocation id as key."""
    shared = f"legacy-{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as session:
        session.add(IntegrationJob(
            organization_id=zendesk_env["org_a_id"], dedupe_key=shared,
            job_type="zendesk.ticket_event",
            payload={"invocation_id": shared, "event_type": "ticket.created",
                     "zendesk_ticket_id": 1, "payload": {}},
        ))
        await session.commit()
    b = base._sign(zendesk_env["secret_b"], _payload(910_006))
    async with make_client() as client:
        rb = await _post(client, zendesk_env["integration_b"], b["content"], _headers(b, shared))
    assert rb.status_code == 202 and rb.json()["duplicate"] is False


@pytest.mark.asyncio
async def test_legacy_job_of_the_same_tenant_still_deduplicates_a_retry(make_client, zendesk_env):
    """In-flight retries across the deploy must not double-process."""
    legacy = f"legacy-same-{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as session:
        job = IntegrationJob(
            organization_id=zendesk_env["org_a_id"], dedupe_key=legacy,
            job_type="zendesk.ticket_event",
            payload={"invocation_id": legacy, "event_type": "ticket.created",
                     "zendesk_ticket_id": 1, "payload": {}},
        )
        session.add(job)
        await session.commit()
        legacy_id = job.id
    a = base._sign(zendesk_env["secret_a"], _payload(910_007))
    async with make_client() as client:
        ra = await _post(client, zendesk_env["integration_a"], a["content"], _headers(a, legacy))
    assert ra.status_code == 202 and ra.json()["duplicate"] is True
    assert ra.json()["job_id"] == legacy_id


@pytest.mark.asyncio
async def test_concurrent_cross_tenant_collision_creates_one_job_each(make_client, zendesk_env):
    shared = f"conc-{uuid.uuid4().hex}"
    a = base._sign(zendesk_env["secret_a"], _payload(910_008))
    b = base._sign(zendesk_env["secret_b"], _payload(910_009))
    async with make_client() as client:
        responses = await asyncio.gather(
            *[
                _post(client, zendesk_env["integration_a"], a["content"], _headers(a, shared))
                for _ in range(4)
            ],
            *[
                _post(client, zendesk_env["integration_b"], b["content"], _headers(b, shared))
                for _ in range(4)
            ],
        )
    assert {r.status_code for r in responses} == {202}
    assert len(await _jobs_for(zendesk_env["org_a_id"])) == 1
    assert len(await _jobs_for(zendesk_env["org_b_id"])) == 1


# --- malformed / hostile headers must be 4xx, never 500 --------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "signature",
    ["é" * 20, "ÿþ", "💥".encode().decode("latin-1"), "A" * 4096, "=", "%%%%"],
)
async def test_non_ascii_or_oversized_signature_header_is_401_not_500(
    signature, make_client, zendesk_env
):
    signed = base._sign(zendesk_env["secret_a"], _payload(910_010))
    headers = _headers(signed, f"sig-{uuid.uuid4().hex}")
    headers["X-Zendesk-Webhook-Signature"] = signature
    async with make_client() as client:
        r = await _post(client, zendesk_env["integration_a"], signed["content"], headers)
    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timestamp",
    ["", " ", "0", "-1", "99999999999999", "\u0663\u0664\u0665", "NaN", "2026-13-45T99:99:99Z",
     "9" * 200, "2026-10-15T12:00:00+99:99", "'; DROP TABLE x;--"],
)
async def test_malformed_timestamps_are_rejected_without_500(timestamp, make_client, zendesk_env):
    signed = base._sign(zendesk_env["secret_a"], _payload(910_011))
    headers = _headers(signed, f"ts-{uuid.uuid4().hex}")
    headers["X-Zendesk-Webhook-Signature-Timestamp"] = timestamp
    async with make_client() as client:
        r = await _post(client, zendesk_env["integration_a"], signed["content"], headers)
    assert r.status_code in (400, 401), r.status_code
    assert await _jobs_for(zendesk_env["org_a_id"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invocation",
    ["x" * 129, "x" * 5000, "has space", "semi;colon", "slash/slash", "ünicode", "a\tb"],
)
async def test_invalid_invocation_ids_are_400_before_any_work(invocation, make_client, zendesk_env):
    signed = base._sign(zendesk_env["secret_a"], _payload(910_012))
    async with make_client() as client:
        r = await _post(client, zendesk_env["integration_a"], signed["content"],
                        _headers(signed, invocation))
    assert r.status_code == 400
    assert await _jobs_for(zendesk_env["org_a_id"]) == []


@pytest.mark.asyncio
async def test_oversized_body_is_rejected_before_it_is_buffered(make_client, zendesk_env):
    big = b'{"type":"zen:event-type:ticket.created","detail":{"id":1},"pad":"' + b"a" * (2 * 1024 * 1024) + b'"}'
    secret = zendesk_env["secret_a"]
    ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    sig = base64.b64encode(hmac.new(secret.encode(), ts.encode() + big, hashlib.sha256).digest()).decode()
    async with make_client() as client:
        r = await _post(client, zendesk_env["integration_a"], big, {
            "Content-Type": "application/json",
            "X-Zendesk-Webhook-Signature": sig,
            "X-Zendesk-Webhook-Signature-Timestamp": ts,
            "X-Zendesk-Webhook-Invocation-Id": f"big-{uuid.uuid4().hex}",
        })
    assert r.status_code == 413
    assert await _jobs_for(zendesk_env["org_a_id"]) == []


# --- secret rotation --------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_rotation_the_old_secret_and_its_captured_deliveries_stop_working(
    make_client, zendesk_env, db
):
    old = base._sign(zendesk_env["secret_a"], _payload(910_013))
    async with make_client() as client:
        first = await _post(client, zendesk_env["integration_a"], old["content"],
                            _headers(old, f"rot-{uuid.uuid4().hex}"))
    assert first.status_code == 202

    token = zendesk_env["token_a"]
    token.webhook_secret = f"rotated-{uuid.uuid4().hex[:20]}"
    db.add(token)
    await db.commit()

    async with make_client() as client:
        replay = await _post(client, zendesk_env["integration_a"], old["content"],
                             _headers(old, f"rot-{uuid.uuid4().hex}"))
        fresh = base._sign(token.webhook_secret, _payload(910_014))
        ok = await _post(client, zendesk_env["integration_a"], fresh["content"],
                         _headers(fresh, f"rot-{uuid.uuid4().hex}"))
    assert replay.status_code == 401
    assert ok.status_code == 202 and ok.json()["duplicate"] is False


# --- storage invariants -------------------------------------------------------------


@pytest.mark.asyncio
async def test_fingerprint_is_unique_nullable_and_never_exposed(make_client, zendesk_env):
    signed = base._sign(zendesk_env["secret_a"], _payload(910_015))
    async with make_client() as client:
        r = await _post(client, zendesk_env["integration_a"], signed["content"],
                        _headers(signed, f"fp-{uuid.uuid4().hex}"))
    assert set(r.json()) == {"event_id", "job_id", "duplicate", "status"}
    (job,) = await _jobs_for(zendesk_env["org_a_id"])
    assert job.signed_delivery_fingerprint and len(job.signed_delivery_fingerprint) == 64
    assert "signed_delivery_fingerprint" not in json.dumps(job.payload)
    async with AsyncSessionLocal() as s:
        dupes = await s.scalar(
            select(func.count()).select_from(IntegrationJob).where(
                IntegrationJob.signed_delivery_fingerprint == job.signed_delivery_fingerprint
            )
        )
    assert dupes == 1


# --- downstream: a foreign TicketEvent is never reused ------------------------------


@pytest.mark.asyncio
async def test_processing_never_reuses_another_tenants_ticket_event(zendesk_env, db, monkeypatch):
    from app.models.ticket import Ticket
    from app.models.ticket_event import TicketEvent
    from app.services.integration_job_service import PermanentJobError
    from app.services.zendesk_webhook_service import ZendeskWebhookService

    foreign = Ticket(
        organization_id=zendesk_env["org_b_id"], subject="B", description="B",
        status="open", priority="normal", source="zendesk", external_id="777001",
    )
    db.add(foreign)
    await db.commit()
    key = f"event-{uuid.uuid4().hex}"
    event = TicketEvent(
        event_key=key, event_type="ticket.created", source="zendesk",
        ticket_id=foreign.id, payload={}, writeback_completed=True,
    )
    db.add(event)
    await db.commit()

    try:
        with pytest.raises(PermanentJobError):
            await ZendeskWebhookService.process(
                db, invocation_id=key, event_type="ticket.created", zendesk_ticket_id=1,
                payload={}, organization_id=zendesk_env["org_a_id"],
            )
    finally:
        await db.rollback()
        await db.delete(event)
        await db.delete(foreign)
        await db.commit()
