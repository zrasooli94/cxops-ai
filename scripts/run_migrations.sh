#!/bin/sh
# Run database migrations as a single explicit step.
#
# Never run this from a service start command. Every replica of the web service
# would race on the same DDL, which produces "relation already exists" or
# "duplicate key value violates unique constraint" errors that look like
# application bugs.
#
# Usage (from a one-off shell or a CI deploy job, with DATABASE_URL set):
#   scripts/run_migrations.sh
#
# The advisory lock is held by the same process that runs Alembic, so a second
# migrator waits instead of interleaving DDL with this one. If the lock cannot
# be taken within MIGRATION_LOCK_WAIT_SECONDS the script fails loudly rather
# than blocking a deploy job forever.
#
# Verify afterwards with scripts/validate_production_config.py, which compares
# `alembic current` to `alembic heads` without mutating anything.
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "error: DATABASE_URL must be set" >&2
  exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname "$SCRIPT_DIR")
cd "$REPO_ROOT"

echo "Acquiring advisory migration lock and applying migrations..."
MIGRATION_LOCK_WAIT_SECONDS="${MIGRATION_LOCK_WAIT_SECONDS:-300}" python - <<'PY'
"""Hold a session advisory lock for the whole duration of the migration.

The lock must be held by the connection that stays open while Alembic runs.
Acquiring it in a short-lived helper process and then closing that connection
would release it immediately, which is exactly the race this script exists to
prevent.
"""

import asyncio
import concurrent.futures
import os
import sys
import time

import asyncpg
from alembic import command
from alembic.config import Config

# Arbitrary fixed key shared by every migrator of this application. Changing it
# would let a new process bypass the lock held by an in-flight old one.
MIGRATION_LOCK_KEY = 728_120_001
LOCK_WAIT_SECONDS = int(os.environ.get("MIGRATION_LOCK_WAIT_SECONDS", "300"))


async def acquire_lock(dsn: str) -> asyncpg.Connection:
    """Connect and take the session lock, polling until the deadline.

    Polling with pg_try_advisory_lock keeps the wait bounded and reportable
    instead of an uninterruptible blocking call.
    """
    connection = await asyncpg.connect(dsn)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    announced = False
    while True:
        acquired = await connection.fetchval(
            "SELECT pg_try_advisory_lock($1)", MIGRATION_LOCK_KEY
        )
        if acquired:
            if announced:
                print("lock acquired after waiting")
            else:
                print("lock acquired")
            return connection
        if time.monotonic() >= deadline:
            await connection.close()
            raise TimeoutError(
                f"another migration has held the advisory lock for more than "
                f"{LOCK_WAIT_SECONDS}s; refusing to start a second migrator"
            )
        if not announced:
            print("another migration holds the lock; waiting...")
            announced = True
        await asyncio.sleep(1)


def main() -> int:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)

    # The loop is created and driven manually rather than with asyncio.run(),
    # because it must stay alive (idle) while Alembic migrates in a worker
    # thread. alembic/env.py calls asyncio.run() itself, so the worker thread
    # must not inherit a running loop.
    loop = asyncio.new_event_loop()
    try:
        connection = loop.run_until_complete(acquire_lock(dsn))
    except TimeoutError as exc:
        loop.close()
        print(f"error: {exc}", file=sys.stderr)
        return 1

    failed = True
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            config = Config(os.path.join(os.getcwd(), "alembic.ini"))
            pool.submit(command.upgrade, config, "head").result()
        failed = False
    finally:
        try:
            loop.run_until_complete(
                connection.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_KEY)
            )
        finally:
            loop.run_until_complete(connection.close())
            loop.close()

    if failed:
        return 1
    print("migrations applied")
    return 0


sys.exit(main())
PY

echo "Migrations applied. Confirm with scripts/validate_production_config.py."
