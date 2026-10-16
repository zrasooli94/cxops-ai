import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)

from app.api.router import api_router
from app.core.build_info import (
    boot_seconds,
    import_phase_seconds,
    mark_boot_complete,
    mark_import_complete,
    uptime_seconds,
)
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.rbac import AuthorizationError, MissingCapabilityError
from app.core.request_context import (
    RequestCorrelationMiddleware,
)
from staging.http_gate import staging_request_allowed

log = get_logger(__name__)

configure_logging()

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Resume public-chat turns interrupted by a restart, then sweep hourly.

    Runs in the background so a slow or unreachable database never delays the
    process from serving (Phase 1P.9, hardened in 1P.9R).
    """
    from app.core.database import engine
    from app.core.schema_gate import check_schema
    # Phase 1P.9R: refuse to serve on a schema this build does not support
    # (raises in production; uvicorn then exits before binding its port).
    if settings.handoff_notification_config_error:
        log.warning(
            "handoff_notifications_disabled_by_config",
            reason=settings.handoff_notification_config_error,
        )
    mark_import_complete()  # everything is imported; the schema gate is not "import"
    started = time.perf_counter()
    await check_schema(engine)
    mark_boot_complete()
    log.info(
        "startup_complete",
        schema_gate_ms=round((time.perf_counter() - started) * 1000, 1),
        process_uptime_s=uptime_seconds(),
        boot_seconds=boot_seconds(),
        app_import_seconds=import_phase_seconds(),
    )

    # Staging is deliberately passive until the empty, isolated database,
    # provider-disabled API and identity enrollment pass independent review.
    # Never start turn maintenance, notification dispatch or OpenAI warmup
    # merely because a Free Render staging instance has come online.
    if settings.environment == "staging":
        log.info("staging_passive_runtime_no_workers_or_provider_warmup")
        yield
        return

    from app.services.provider_warmup import warm_provider_clients
    from app.services.public_chat_turn_dispatcher import (
        handoff_dispatcher,
        turn_dispatcher,
    )

    loop = asyncio.get_running_loop()
    task = loop.create_task(turn_dispatcher.run_maintenance())
    notification_task = (
        loop.create_task(handoff_dispatcher.run_maintenance())
        if settings.handoff_notifications_enabled else None
    )
    warm_task = loop.create_task(warm_provider_clients())
    try:
        yield
    finally:
        warm_task.cancel()
        task.cancel()
        if notification_task is not None:
            notification_task.cancel()
            await handoff_dispatcher.shutdown(timeout=5.0)
        await turn_dispatcher.drain(timeout=10.0)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(RequestCorrelationMiddleware)


@app.middleware("http")
async def staging_read_only_access_gate(
    request: Request,
    call_next,
):
    """No outbound or mutating routes while staging is being qualified.

    A missing or forged identity cannot bypass this first-pass HTTP gate.
    If/when synthetic read-only staging launches, the existing authentication
    and tenant membership dependencies still apply to every staff route.
    """
    # This experimental route is invisible in *every* production request,
    # including unauthenticated requests. Reject before JWT, tenant lookup,
    # route dependencies or any database access can occur.
    if (
        request.url.path.rstrip("/").startswith("/staging/ticket-action")
        and settings.environment != "staging"
    ):
        return JSONResponse(
            status_code=404,
            content={"detail": "Not found"},
            headers={"Cache-Control": "no-store"},
        )
    if settings.environment == "staging" and not staging_request_allowed(
        request.method, request.url.path
    ):
        return JSONResponse(
            status_code=503,
            content={"detail": "Staging operations are not enabled."},
            headers={"Cache-Control": "no-store"},
        )
    return await call_next(request)


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

# Phase 1U-F: enrich only the generated OpenAPI document. The actual
# Authorization headers, JWT validation, membership lookup and RBAC
# dependencies are unchanged. Visitor chat bearer != staff identity JWT.
from app.core.openapi_security import document_authentication

_builtin_openapi = app.openapi


def _cxops_openapi() -> dict:
    return document_authentication(app, _builtin_openapi())


app.openapi = _cxops_openapi  # type: ignore[method-assign]

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
