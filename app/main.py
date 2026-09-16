from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)
from sqlalchemy import text

from app.api.router import api_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import configure_logging, get_logger
from app.core.rbac import AuthorizationError, MissingCapabilityError
from app.core.request_context import (
    RequestCorrelationMiddleware,
)

log = get_logger(__name__)

configure_logging()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)

app.add_middleware(RequestCorrelationMiddleware)


@app.exception_handler(AuthorizationError)
async def authorization_error_handler(
    request: Request,
    exc: AuthorizationError,
) -> JSONResponse:
    """Map capability and role failures to a generic 403.

    Keeps the existing fail-closed semantics for service-layer guards that
    raise ``AuthorizationError`` subclasses. Logs the denial so that
    defense-in-depth service-layer failures are auditable even when no route
    guard was involved.
    """
    extra: dict = {"route": request.url.path}
    if isinstance(exc, MissingCapabilityError):
        extra["reason"] = "missing_capability"
    log.warning(
        "authorization_error_handler",
        detail=str(exc),
        **extra,
    )
    return JSONResponse(
        status_code=403,
        content={"detail": "Insufficient permissions"},
    )


app.include_router(api_router)


@app.get(
    "/health",
    include_in_schema=False,
)
async def healthcheck():
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT 1"))

    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "database": "ok",
    }


@app.get(
    "/metrics",
    include_in_schema=False,
)
async def prometheus_metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
