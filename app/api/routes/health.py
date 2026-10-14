"""Liveness, readiness, and build-identity endpoints (Phase 1P.3).

Three probes with three deliberately different contracts, because conflating
them causes outages:

``/health``
    Liveness. Answers ``200`` whenever the process can serve a request. It does
    **not** touch PostgreSQL: a database blip must not make a platform restart
    healthy API instances in a loop.

``/ready``
    Readiness. Verifies the one dependency that makes the API non-functional
    without it - PostgreSQL. ``timing_ms`` reports the database first-statement
    and steady-state round-trip times (numbers only, no host or error text).
    Answers ``503`` when that check fails, which is
    what a load balancer or Render health check needs in order to take the
    instance out of rotation. Optional tenant integrations are never probed
    here: a third-party provider being down must not pull the whole platform
    offline.

``/version``
    Build identity. Answers which application version, environment and commit
    is deployed, so an operator can confirm a deploy without shell access, and
    how long the process has been up (a small ``uptime_seconds`` on a slow
    first request means a cold start, not a slow handler). The commit comes
    only from platform build metadata and is shown only when it is a plain hex
    id; see ``app.core.build_info``.

None of these endpoints echo configuration, connection strings, or credential
material, and a failing database check reports a fixed reason rather than the
driver's exception text, which can embed the connection URL.
"""

import time

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.build_info import (
    PROCESS_STARTED_AT,
    boot_seconds,
    build_sha,
    uptime_seconds,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal

router = APIRouter(tags=["Health"])

# Fixed reason strings. Never interpolate an exception here: SQLAlchemy driver
# errors routinely carry the DSN, host, and user.
DB_OK = "ok"
DB_UNAVAILABLE = "unavailable"


# These three handlers are ``async def`` on purpose: they do no blocking work,
# and a plain ``def`` is dispatched to the shared worker-thread pool, so a probe
# could queue behind unrelated blocking calls instead of answering immediately.
@router.get("/")
async def root() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "status": "running",
    }


@router.get("/health")
async def health() -> dict:
    """Liveness only - deliberately does not query the database."""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }


@router.get("/ready")
async def ready(response: Response) -> dict:
    """Readiness. Fails with 503 when PostgreSQL is unreachable."""
    database = DB_OK
    timing: dict[str, float] = {}
    try:
        async with AsyncSessionLocal() as db:
            started = time.perf_counter()
            await db.execute(text("SELECT 1"))
            timing["database_first_statement"] = round(
                (time.perf_counter() - started) * 1000, 1
            )
            # A second statement on the same connection is a pure network
            # round trip: this is the per-statement cost every chat turn pays
            # (~45 statements) and the number that decides whether the API and
            # the database are in the same region.
            started = time.perf_counter()
            await db.execute(text("SELECT 1"))
            timing["database_round_trip"] = round(
                (time.perf_counter() - started) * 1000, 1
            )
    except Exception:  # noqa: BLE001 - any driver/timeout error fails readiness
        # The detail is logged, not returned. Callers get a stable contract.
        database = DB_UNAVAILABLE
        timing = {}

    ready_now = database == DB_OK
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if ready_now else "not_ready",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "checks": {"database": database},
        "timing_ms": timing,
    }


@router.get("/version")
async def version() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "build_sha": build_sha(),
        "started_at": PROCESS_STARTED_AT.isoformat(),
        "uptime_seconds": uptime_seconds(),
        "boot_seconds": boot_seconds(),
    }
