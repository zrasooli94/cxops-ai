from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import worker_clock as clock_route
from app.core import database
from app.core.config import Settings, settings
from app.core.database import get_db
from app.main import app
from app.models.worker_clock_tick import WorkerClockTick
from app.repositories.worker_clock_repository import WorkerClockRepository
from scripts.worker_clock_protocol import CLOCK_PATH, SLOT_MS

NOW_MS = 1_800_000_000_000
CURRENT_SECRET = "c" * 40
PREVIOUS_SECRET = "p" * 40
NONCE = "11111111-1111-4111-8111-111111111111"


def _signed_request(
    *,
    timestamp: int = NOW_MS,
    secret: str = CURRENT_SECRET,
    key_id: str = "current",
) -> tuple[bytes, list[tuple[str, str]]]:
    slot = timestamp // SLOT_MS
    body = json.dumps({"version": 1, "slot": slot}, separators=(",", ":")).encode()
    digest = hashlib.sha256(body).hexdigest()
    canonical = (
        f"cxops-clock-v1\nPOST\n{CLOCK_PATH}\n{timestamp}\n{NONCE}\n{key_id}\n{digest}"
    )
    signature = hmac.new(
        secret.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    return body, [
        ("content-type", "application/json"),
        ("x-cxops-clock-time", str(timestamp)),
        ("x-cxops-clock-nonce", NONCE),
        ("x-cxops-clock-key-id", key_id),
        ("x-cxops-clock-signature", f"v1={signature}"),
    ]


@pytest.fixture(autouse=True)
async def _clock_state(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    await db.execute(delete(WorkerClockTick))
    await db.commit()
    monkeypatch.setattr(settings, "worker_clock_receiver_enabled", True)
    monkeypatch.setattr(settings, "worker_clock_executor_enabled", False)
    monkeypatch.setattr(settings, "worker_clock_id", "test-clock")
    monkeypatch.setattr(settings, "worker_clock_current_key_id", "current")
    monkeypatch.setattr(settings, "worker_clock_current_secret", CURRENT_SECRET)
    monkeypatch.setattr(settings, "worker_clock_previous_key_id", "previous")
    monkeypatch.setattr(settings, "worker_clock_previous_secret", PREVIOUS_SECRET)
    monkeypatch.setattr(
        settings,
        "worker_clock_previous_key_expires_at",
        datetime(2100, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(settings, "worker_clock_max_catchup_slots", 12)
    monkeypatch.setattr(clock_route.time, "time", lambda: NOW_MS / 1000)


@pytest.fixture
async def client(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as api_client:
            yield api_client
    finally:
        app.dependency_overrides.clear()


async def test_disabled_receiver_is_404_before_auth_or_database(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "worker_clock_receiver_enabled", False)

    async def _must_not_record(*_args, **_kwargs):
        raise AssertionError("disabled receiver touched the ledger")

    monkeypatch.setattr(WorkerClockRepository, "record_tick", _must_not_record)
    response = await client.post(CLOCK_PATH, content=b"not-signed")
    assert response.status_code == 404


async def test_valid_and_duplicate_delivery_are_indistinguishable_and_durable(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    body, headers = _signed_request()
    first = await client.post(CLOCK_PATH, content=body, headers=headers)
    duplicate = await client.post(CLOCK_PATH, content=body, headers=headers)
    assert first.status_code == duplicate.status_code == 202
    assert first.json() == duplicate.json() == {"status": "accepted"}
    count = await db.scalar(select(func.count()).select_from(WorkerClockTick))
    assert count == 1


async def test_previous_rotation_key_is_bounded_and_unknown_key_is_rejected(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    body, headers = _signed_request(secret=PREVIOUS_SECRET, key_id="previous")
    assert (
        await client.post(CLOCK_PATH, content=body, headers=headers)
    ).status_code == 202
    body, headers = _signed_request(key_id="attacker")
    assert (
        await client.post(CLOCK_PATH, content=body, headers=headers)
    ).status_code == 401
    count = await db.scalar(select(func.count()).select_from(WorkerClockTick))
    assert count == 1


@pytest.mark.parametrize("key_id", ["UPPER", "has_underscore", "-leading"])
def test_settings_reject_key_ids_outside_wire_format(key_id: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            worker_clock_receiver_enabled=True,
            worker_clock_current_key_id=key_id,
            worker_clock_current_secret=CURRENT_SECRET,
        )


@pytest.mark.parametrize(
    ("clock_id", "slot", "attempts", "max_attempts"),
    [
        ("", 100, 0, 3),
        ("   ", 100, 0, 3),
        ("constraint-clock", 0, 0, 3),
        ("constraint-clock", 100, 4, 3),
    ],
)
async def test_database_rejects_invalid_ledger_state(
    db: AsyncSession,
    clock_id: str,
    slot: int,
    attempts: int,
    max_attempts: int,
) -> None:
    db.add(
        WorkerClockTick(
            clock_id=clock_id,
            slot=slot,
            scheduled_at=datetime.now(UTC),
            attempts=attempts,
            max_attempts=max_attempts,
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_expired_previous_rotation_key_is_rejected(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "worker_clock_previous_key_expires_at",
        datetime(2020, 1, 1, tzinfo=UTC),
    )
    body, headers = _signed_request(secret=PREVIOUS_SECRET, key_id="previous")
    assert (
        await client.post(CLOCK_PATH, content=body, headers=headers)
    ).status_code == 401


async def test_raw_duplicate_header_query_and_oversize_body_fail_closed(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    body, headers = _signed_request()
    duplicated = [
        *headers,
        ("X-CXOps-Clock-Signature", dict(headers)["x-cxops-clock-signature"]),
    ]
    assert (
        await client.post(CLOCK_PATH, content=body, headers=duplicated)
    ).status_code == 401
    assert (
        await client.post(f"{CLOCK_PATH}?force=1", content=body, headers=headers)
    ).status_code == 401
    assert (
        await client.post(CLOCK_PATH, content=b"x" * 129, headers=headers)
    ).status_code == 413
    count = await db.scalar(select(func.count()).select_from(WorkerClockTick))
    assert count == 0


async def test_concurrent_duplicate_claims_insert_exactly_one_slot() -> None:
    async def record():
        async with database.AsyncSessionLocal() as session:
            return await WorkerClockRepository.record_tick(
                session, clock_id="concurrent-clock", slot=6000000, max_catchup_slots=12
            )

    first, second = await asyncio.gather(record(), record())
    assert sorted((first.accepted, second.accepted)) == [False, True]
    async with database.AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(WorkerClockTick)
            .where(WorkerClockTick.clock_id == "concurrent-clock")
        )
    assert count == 1


async def test_missed_slots_are_bounded_then_coalesced_to_one_execution(
    db: AsyncSession,
) -> None:
    await WorkerClockRepository.record_tick(
        db, clock_id="catchup-clock", slot=100, max_catchup_slots=12
    )
    recovered = await WorkerClockRepository.record_tick(
        db, clock_id="catchup-clock", slot=103, max_catchup_slots=12
    )
    assert recovered.inserted_slots == 3
    assert recovered.recovered_slots == 2
    tick = await WorkerClockRepository.claim_next(db, clock_id="catchup-clock")
    assert tick is not None and tick.slot == 103
    statuses = dict(
        (
            await db.execute(
                select(WorkerClockTick.slot, WorkerClockTick.status).where(
                    WorkerClockTick.clock_id == "catchup-clock"
                )
            )
        ).all()
    )
    assert statuses == {
        100: "coalesced",
        101: "coalesced",
        102: "coalesced",
        103: "processing",
    }


async def test_large_gap_records_only_bounded_tail_and_truncation(
    db: AsyncSession,
) -> None:
    await WorkerClockRepository.record_tick(
        db, clock_id="bounded-clock", slot=100, max_catchup_slots=2
    )
    result = await WorkerClockRepository.record_tick(
        db, clock_id="bounded-clock", slot=110, max_catchup_slots=2
    )
    assert result.catchup_truncated is True
    slots = (
        (
            await db.execute(
                select(WorkerClockTick.slot)
                .where(WorkerClockTick.clock_id == "bounded-clock")
                .order_by(WorkerClockTick.slot)
            )
        )
        .scalars()
        .all()
    )
    assert slots == [100, 108, 109, 110]
