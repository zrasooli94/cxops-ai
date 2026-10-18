from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.repositories.worker_clock_repository import WorkerClockRepository
from scripts.worker_clock_protocol import (
    CLOCK_PATH,
    InvalidClockTrigger,
    verify_clock_trigger,
)

router = APIRouter(tags=["internal-worker-clock"])
log = get_logger(__name__)

_SECURITY_HEADERS = {
    "content-length",
    "content-type",
    "host",
    "origin",
    "referer",
    "x-cxops-clock-key-id",
    "x-cxops-clock-nonce",
    "x-cxops-clock-signature",
    "x-cxops-clock-time",
}
_CLOCK_HEADERS = {
    "x-cxops-clock-key-id",
    "x-cxops-clock-nonce",
    "x-cxops-clock-signature",
    "x-cxops-clock-time",
}


def _raw_headers(request: Request) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_name, raw_value in request.scope.get("headers", []):
        try:
            name = raw_name.decode("ascii").lower()
            value = raw_value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise InvalidClockTrigger("malformed header") from exc
        if name in _SECURITY_HEADERS:
            if name in seen or "," in value:
                raise InvalidClockTrigger("duplicated clock header")
            seen.add(name)
        if name.startswith("x-cxops-clock-"):
            if name not in _CLOCK_HEADERS:
                raise InvalidClockTrigger("unknown clock header")
            pairs.append((name, value))
    if seen.intersection({"origin", "referer"}):
        raise InvalidClockTrigger("browser request rejected")
    if request.headers.get("content-type") != "application/json":
        raise InvalidClockTrigger("invalid content type")
    return pairs


async def _bounded_body(request: Request, limit: int = 128) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    return bytes(body)


def _secret_for_key_id(key_id: str) -> str:
    if key_id == settings.worker_clock_current_key_id:
        return settings.worker_clock_current_secret
    if (
        settings.worker_clock_previous_key_id
        and key_id == settings.worker_clock_previous_key_id
        and settings.worker_clock_previous_key_expires_at is not None
        and datetime.now(UTC) <= settings.worker_clock_previous_key_expires_at
    ):
        return settings.worker_clock_previous_secret
    raise InvalidClockTrigger("unknown clock key")


@router.post(CLOCK_PATH, include_in_schema=False, status_code=status.HTTP_202_ACCEPTED)
async def receive_worker_clock_tick(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    """Authenticate and durably record one clock intent; never execute work."""
    if not settings.worker_clock_receiver_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if request.scope.get("raw_path", b"") != CLOCK_PATH.encode() or request.url.query:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        headers = _raw_headers(request)
        header_map = dict(headers)
        secret = _secret_for_key_id(header_map.get("x-cxops-clock-key-id", ""))
        body = await _bounded_body(request)
        slot = verify_clock_trigger(
            method=request.method,
            path=request.url.path,
            headers=headers,
            body=body,
            secret=secret,
            now_ms=int(time.time() * 1000),
        )
    except InvalidClockTrigger as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc

    try:
        result = await WorkerClockRepository.record_tick(
            db,
            clock_id=settings.worker_clock_id,
            slot=slot,
            max_catchup_slots=settings.worker_clock_max_catchup_slots,
        )
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error("worker_clock_tick_persistence_failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc

    if result.catchup_truncated:
        log.warning(
            "worker_clock_catchup_truncated",
            inserted_slots=result.inserted_slots,
            recovered_slots=result.recovered_slots,
        )
    return {"status": "accepted"}
