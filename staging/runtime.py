"""Initialize only the independently empty staging database, then serve read-only.

Existing databases are never upgraded on startup: only an empty public schema
with an explicit one-time opt-in may run the repository's Alembic chain.
Production launchers never import or invoke this module.
"""
from __future__ import annotations

import asyncio
import os


def initialization_action(
    *, environment: str, public_tables: int, current: frozenset[str],
    heads: frozenset[str], allow_empty: bool,
) -> str:
    if environment != "staging":
        raise RuntimeError("This launcher is only available in isolated staging")
    if len(heads) != 1:
        raise RuntimeError("Staging requires exactly one repository schema head")
    if public_tables == 0 and not current and allow_empty:
        return "initialize"
    if public_tables > 0 and current == heads:
        return "serve"
    raise RuntimeError("Staging schema requires explicit operator review")


async def initialize_empty_database() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings
    from app.core.schema_gate import build_revisions, read_current

    # Settings validates the exact staging database, role, TLS and absence of
    # live provider secrets before any database connection is constructed.
    if settings.environment != "staging" or settings.auth_mode != "neon_staging":
        raise RuntimeError("This launcher cannot start outside isolated staging")
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            # All instances of this staging launcher cooperate on the same
            # session lock. Alembic uses a separate connection while it is held.
            await conn.execute(text("SELECT pg_advisory_lock(173019, 1)"))
            try:
                count = int((await conn.execute(text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE'"
                ))).scalar_one())
                heads, _ = build_revisions()
                action = initialization_action(
                    environment=settings.environment, public_tables=count,
                    current=await read_current(engine), heads=heads,
                    allow_empty=os.environ.get("CXOPS_STAGING_INITIALIZE_EMPTY_DB") == "true",
                )
                if action == "initialize":
                    from alembic.config import Config

                    from alembic import command

                    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
                    if await read_current(engine) != heads:
                        raise RuntimeError("Staging migration did not reach the exact schema head")
                print("Isolated staging schema verified; provider and worker actions remain disabled", flush=True)
            finally:
                await conn.execute(text("SELECT pg_advisory_unlock(173019, 1)"))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(initialize_empty_database())
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ["PORT"]), workers=1)


if __name__ == "__main__":
    main()
