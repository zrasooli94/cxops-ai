from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)

from app.api.router import api_router
from app.core.config import settings
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


@app.middleware("http")
async def public_chat_security_headers(
    request: Request,
    call_next,
):
    """Scope minimal security headers to the public web-chat API.

    Public chat responses are tenant-identifying chat data for anonymous
    visitors, so they are never cached; they carry no raw provider payloads and
    no IPs. Headers are deliberately NOT applied globally (control-center
    behavior stays unchanged).
    """
    if not request.url.path.startswith("/public/chat"):
        return await call_next(request)

    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


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

# NOTE (Phase 1P.3): liveness (/health), readiness (/ready), and build identity
# (/version) all live in app/api/routes/health.py. An earlier database-aware
# /health was registered here *after* the router, so the router's static route
# shadowed it and the database check was unreachable dead code - which also made
# scripts/check_live_demo_readiness.py fail against a live server. Do not
# re-add a /health here; add probe behaviour to the health router instead.


@app.get(
    "/metrics",
    include_in_schema=False,
)
async def prometheus_metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
