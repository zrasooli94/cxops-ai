"""Start-up schema compatibility gate (Phase 1P.9R).

Render and Vercel deploy ``master`` automatically, and the Render launchers
deliberately never run migrations. So a merge whose code needs a schema change
can start before the operator has run ``scripts/run_migrations.sh``. Without a
gate, that build would boot "healthy" and then fail every query touching the
new columns - in front of customers.

The gate compares the database's ``alembic_version`` with this build's
migration history *before the process serves or consumes anything*:

* at this build's head .................. start;
* behind it (a known older revision) ..... refuse to start (migration first);
* ahead of it (a revision this build has
  never heard of) ........................ start with a warning - this is a code
  rollback after an expand-only migration, which the expand/contract policy
  keeps compatible;
* no version recorded ................... refuse to start.

On Render a process that exits during start-up never passes its health check,
so the previous deploy keeps serving: deploying code before its migration
becomes a failed deploy instead of an outage. Enforced in production; other
environments only log.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


class SchemaBehindError(RuntimeError):
    """The database has not been migrated to what this build requires."""


@dataclass(frozen=True)
class SchemaStatus:
    verdict: str  # "at_head" | "behind" | "ahead" | "unversioned"
    current: frozenset[str]
    head: frozenset[str]


def build_revisions() -> tuple[frozenset[str], frozenset[str]]:
    """(heads, every revision) of this build's migration scripts."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    heads = frozenset(script.get_heads())
    known = frozenset(rev.revision for rev in script.walk_revisions())
    return heads, known


def classify(
    current: frozenset[str], heads: frozenset[str], known: frozenset[str]
) -> str:
    if not current:
        return "unversioned"
    if current == heads:
        return "at_head"
    if current - known:
        return "ahead"
    return "behind"


async def read_current(engine: AsyncEngine) -> frozenset[str]:
    async with engine.connect() as conn:
        exists = (
            await conn.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL"))
        ).scalar()
        if not exists:
            return frozenset()
        rows = await conn.execute(text("SELECT version_num FROM alembic_version"))
        return frozenset(str(row[0]) for row in rows)


async def check_schema(engine: AsyncEngine, *, enforce: bool | None = None) -> SchemaStatus:
    """Raise ``SchemaBehindError`` when this build must not start."""
    heads, known = build_revisions()
    current = await read_current(engine)
    status = SchemaStatus(classify(current, heads, known), current, heads)
    must_enforce = settings.environment == "production" if enforce is None else enforce
    fields = {
        "verdict": status.verdict,
        "current": sorted(current),
        "required": sorted(heads),
    }
    if status.verdict == "at_head":
        log.info("schema_gate_ok", **fields)
    elif status.verdict == "ahead":
        log.warning("schema_gate_database_ahead_of_build", **fields)
    else:
        log.error("schema_gate_refused", enforced=must_enforce, **fields)
        if must_enforce:
            raise SchemaBehindError(
                f"database schema {sorted(current) or 'unversioned'} does not provide "
                f"this build's required revision {sorted(heads)}; run "
                "scripts/run_migrations.sh before deploying this code"
            )
    return status
