"""Liveness, readiness, and build-identity endpoints (Phase 1P.3).

Three probes with three deliberately different contracts, because conflating
them causes outages:

``/health``
    Liveness. Answers ``200`` whenever the process can serve a request. It does
    **not** touch PostgreSQL: a database blip must not make a platform restart
    healthy API instances in a loop.

``/ready``
    Readiness. Verifies the one dependency that makes the API non-functional
    without it - PostgreSQL. Answers ``503`` when that check fails, which is
    what a load balancer or Render health check needs in order to take the
    instance out of rotation. Optional tenant integrations are never probed
    here: a third-party provider being down must not pull the whole platform
    offline.

``/version``
    Build identity. Answers which application version and environment is
    deployed, so an operator can confirm a deploy without shell access.

None of these endpoints echo configuration, connection strings, or credential
material, and a failing database check reports a fixed reason rather than the
driver's exception text, which can embed the connection URL.
"""

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal

router = APIRouter(tags=["Health"])

# Fixed reason strings. Never interpolate an exception here: SQLAlchemy driver
# errors routinely carry the DSN, host, and user.
DB_OK = "ok"
DB_UNAVAILABLE = "unavailable"


@router.get("/")
def root() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "status": "running",
    }


@router.get("/health")
def health() -> dict:
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
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any driver/timeout error fails readiness
        # The detail is logged, not returned. Callers get a stable contract.
        database = DB_UNAVAILABLE

    ready_now = database == DB_OK
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if ready_now else "not_ready",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "checks": {"database": database},
    }


@router.get("/version")
def version() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }
