from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)
from sqlalchemy import text

from app.api.router import api_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
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
