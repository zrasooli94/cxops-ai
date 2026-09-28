"""Hold a session advisory lock for the whole duration of a migration.

Invoked by ``scripts/run_migrations.sh`` as a single process, so the connection
that owns the lock is the same one that stays open while Alembic migrates in a
worker thread. Acquiring the lock in a short-lived helper and closing that
connection would release it immediately, which is exactly the race the wrapper
script exists to prevent.

The lock key is a fixed constant. Every migrator of this application must agree
on it; changing it would let a new process bypass a lock held by an in-flight
old one and reintroduce concurrent DDL.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
import time

import asyncpg
from alembic.config import Config

from alembic import command

# Arbitrary fixed key shared by every migrator of this application. Changing it
# would let a new process bypass the lock held by an in-flight old one.
MIGRATION_LOCK_KEY = 728_120_001
LOCK_WAIT_SECONDS = int(os.environ.get("MIGRATION_LOCK_WAIT_SECONDS", "300"))


def normalize_dsn(database_url: str) -> str:
    """Return the URL in the plain ``postgresql://`` form asyncpg expects.

    Settings carry the SQLAlchemy driver suffix; asyncpg refuses to connect
    with it. A URL that already has no suffix is returned unchanged, so this is
    safe to call on either form.
    """
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def acquire_lock(dsn: str) -> asyncpg.Connection:
    """Connect and take the session lock, polling until the deadline.

    Polling with ``pg_try_advisory_lock`` keeps the wait bounded and reportable
    instead of an uninterruptible blocking call.
    """
    connection = await asyncpg.connect(dsn)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    announced = False
    try:
        while True:
            acquired = await connection.fetchval(
                "SELECT pg_try_advisory_lock($1)", MIGRATION_LOCK_KEY
            )
            if acquired:
                print("lock acquired after waiting" if announced else "lock acquired")
                return connection
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"another migration has held the advisory lock for more than "
                    f"{LOCK_WAIT_SECONDS}s; refusing to start a second migrator"
                )
            if not announced:
                print("another migration holds the lock; waiting...")
                announced = True
            await asyncio.sleep(1)
    except BaseException:
        # The connection was opened above, so a timeout or cancellation here
        # would otherwise leak it -- and with it the partially-taken lock.
        await connection.close()
        raise


async def release_lock(connection: asyncpg.Connection) -> None:
    """Release the session lock, then close the connection.

    Order matters: closing the connection would also drop the lock, but doing it
    explicitly keeps the behaviour observable and correct if the close ever
    becomes conditional.
    """
    try:
        await connection.execute(
            "SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_KEY
        )
    finally:
        await connection.close()


def main() -> int:
    dsn = normalize_dsn(os.environ["DATABASE_URL"])

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
        # The lock must be released on the success path and on every failure
        # path, otherwise one failed deploy wedges the next one until the
        # session's own timeout -- which is exactly the "second migrator
        # refuses to start" deadlock the bounded wait would then report.
        try:
            loop.run_until_complete(release_lock(connection))
        finally:
            loop.close()

    if failed:
        return 1
    print("migrations applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
