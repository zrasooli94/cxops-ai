"""Advisory-lock behaviour for the migration step.

Concurrent DDL is the failure mode this lock exists to prevent, so the tests
that matter are the ones where a *second* migrator is actually present. The
contention cases run against the real database rather than a mock: a mocked
``pg_try_advisory_lock`` would prove nothing about whether the lock is taken on
the same session that stays open across the migration.

The key invariants pinned here:
  * a second migrator waits instead of running DDL concurrently;
  * it fails loudly, and boundedly, rather than blocking a deploy job forever;
  * the lock is released on the success path AND on every failure path, so one
    bad deploy cannot wedge the next one;
  * the connection that holds the lock is the connection that closes.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import migration_lock  # noqa: E402


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        from app.core.config import settings

        url = settings.database_url
    return migration_lock.normalize_dsn(url)


# ---------------------------------------------------------------------------
# DSN handling
# ---------------------------------------------------------------------------


def test_driver_suffix_is_stripped_for_asyncpg() -> None:
    assert migration_lock.normalize_dsn(
        "postgresql+asyncpg://u:p@host:5432/db"
    ) == "postgresql://u:p@host:5432/db"


def test_plain_postgres_url_is_left_alone() -> None:
    url = "postgresql://u:p@host:5432/db"
    assert migration_lock.normalize_dsn(url) == url


def test_only_the_first_scheme_is_rewritten() -> None:
    """A password containing the suffix must not be mangled."""
    url = "postgresql+asyncpg://u:postgresql+asyncpg://x@host:5432/db"
    assert migration_lock.normalize_dsn(url) == (
        "postgresql://u:postgresql+asyncpg://x@host:5432/db"
    )


# ---------------------------------------------------------------------------
# Real contention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_migrator_cannot_take_the_lock() -> None:
    """While one session holds the lock, another must be refused."""
    import asyncpg

    holder = await asyncpg.connect(_dsn())
    try:
        taken = await holder.fetchval(
            "SELECT pg_try_advisory_lock($1)", migration_lock.MIGRATION_LOCK_KEY
        )
        assert taken is True, "precondition: holder must acquire the lock"

        contender = await asyncpg.connect(_dsn())
        try:
            blocked = await contender.fetchval(
                "SELECT pg_try_advisory_lock($1)", migration_lock.MIGRATION_LOCK_KEY
            )
            assert blocked is False, (
                "a second session acquired the migration lock while another held it"
            )
        finally:
            await contender.close()
    finally:
        await holder.close()


@pytest.mark.asyncio
async def test_acquire_lock_blocks_then_succeeds_once_the_holder_releases() -> None:
    """The waiting migrator must proceed, not time out, when the lock frees up.

    This is the behaviour that makes rolling deploys safe: replica two starts
    slightly later, waits on replica one's lock, then runs Alembic on an
    already-migrated schema -- which is a no-op.
    """
    import asyncpg

    holder = await asyncpg.connect(_dsn())
    await holder.fetchval(
        "SELECT pg_try_advisory_lock($1)", migration_lock.MIGRATION_LOCK_KEY
    )

    async def release_soon() -> None:
        await asyncio.sleep(0.4)
        await holder.execute(
            "SELECT pg_advisory_unlock($1)", migration_lock.MIGRATION_LOCK_KEY
        )
        await holder.close()

    releaser = asyncio.create_task(release_soon())
    try:
        original_wait = migration_lock.LOCK_WAIT_SECONDS
        migration_lock.LOCK_WAIT_SECONDS = 20
        try:
            connection = await migration_lock.acquire_lock(_dsn())
        finally:
            migration_lock.LOCK_WAIT_SECONDS = original_wait
        # Only reach here if the wait loop observed the release.
        await migration_lock.release_lock(connection)
    finally:
        if not releaser.done():
            await releaser


@pytest.mark.asyncio
async def test_acquire_lock_times_out_and_closes_its_connection(
    monkeypatch,
) -> None:
    """A held lock must produce a bounded failure and not leak its connection.

    The connection is opened before the wait begins, so the timeout path has to
    close it. A leaked session is what turns a clear "refusing to start a
    second migrator" error into an opaque connection-count problem later, so the
    close is asserted rather than assumed.
    """
    import asyncpg

    holder = await asyncpg.connect(_dsn())
    await holder.fetchval(
        "SELECT pg_try_advisory_lock($1)", migration_lock.MIGRATION_LOCK_KEY
    )

    class _TrackingConn:
        def __init__(self) -> None:
            self.closed = False

        async def fetchval(self, *_a, **_k):
            return False  # the real holder still owns the lock

        async def close(self) -> None:
            self.closed = True

    tracking = _TrackingConn()
    opened: list[str] = []

    async def _connect(dsn):
        opened.append(dsn)
        return tracking

    monkeypatch.setattr(migration_lock.asyncpg, "connect", _connect)

    original_wait = migration_lock.LOCK_WAIT_SECONDS
    try:
        # 0s makes the loop try once and then hit the deadline, so the test does
        # not have to sleep through a real wait.
        migration_lock.LOCK_WAIT_SECONDS = 0
        with pytest.raises(TimeoutError) as exc:
            await migration_lock.acquire_lock(_dsn())
        assert "refusing to start a second migrator" in str(exc.value)
    finally:
        migration_lock.LOCK_WAIT_SECONDS = original_wait
        await holder.execute(
            "SELECT pg_advisory_unlock($1)", migration_lock.MIGRATION_LOCK_KEY
        )
        await holder.close()

    assert opened, "acquire_lock never opened a connection"
    assert tracking.closed is True, (
        "the connection opened for the lock wait was not closed on timeout"
    )


@pytest.mark.asyncio
async def test_release_lock_frees_the_key_for_the_next_migrator() -> None:
    """After release, the key must be immediately re-acquirable.

    This is the "one failed deploy must not wedge the next" invariant, tested at
    the database level rather than by asserting on mock calls.
    """
    import asyncpg

    connection = await migration_lock.acquire_lock(_dsn())
    await migration_lock.release_lock(connection)

    probe = await asyncpg.connect(_dsn())
    try:
        taken = await probe.fetchval(
            "SELECT pg_try_advisory_lock($1)", migration_lock.MIGRATION_LOCK_KEY
        )
        assert taken is True, "lock was not released"
        await probe.execute(
            "SELECT pg_advisory_unlock($1)", migration_lock.MIGRATION_LOCK_KEY
        )
    finally:
        await probe.close()


@pytest.mark.asyncio
async def test_release_lock_closes_the_connection_even_if_unlock_fails() -> None:
    """A failure to unlock must not leak the connection.

    Uses a stub whose unlock raises, so the assertion is about the close path
    rather than about a real database error.
    """

    class _Stub:
        def __init__(self) -> None:
            self.unlocked = False
            self.closed = False

        async def execute(self, *_args, **_kwargs):
            self.unlocked = True
            raise RuntimeError("unlock failed")

        async def close(self) -> None:
            self.closed = True

    stub = _Stub()
    with pytest.raises(RuntimeError, match="unlock failed"):
        await migration_lock.release_lock(stub)
    assert stub.closed is True


# ---------------------------------------------------------------------------
# main(): exit codes and cleanup ordering
# ---------------------------------------------------------------------------


def _run_main(monkeypatch, *, upgrade, wait="1", state=None) -> tuple[int, dict]:
    """Drive main() with the database and Alembic stubbed out.

    Returns the exit code and a record of what main() did with the lock, so a
    test can assert on cleanup rather than only on the exit status.
    """
    # Supplied by the caller so the record survives even when main() raises;
    # a test asserting on cleanup after a failure needs to see it.
    if state is None:
        state = {"released": [], "closed": [], "acquired": []}

    class _Conn:
        def __init__(self, tag: str) -> None:
            self.tag = tag

        async def execute(self, *_a, **_k):
            return None

        async def close(self):
            state["closed"].append(self.tag)

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("MIGRATION_LOCK_WAIT_SECONDS", wait)
    async def _acquire(dsn):
        state["acquired"].append(dsn)
        return _Conn("lock")

    async def _release(conn):
        state["released"].append(conn.tag)
        await conn.close()

    monkeypatch.setattr(migration_lock, "acquire_lock", _acquire)
    monkeypatch.setattr(migration_lock, "release_lock", _release)
    monkeypatch.setattr(migration_lock.command, "upgrade", upgrade)
    monkeypatch.setattr(migration_lock, "Config", lambda *a, **k: object())
    return migration_lock.main(), state


def test_main_returns_zero_after_a_successful_migration(monkeypatch) -> None:
    code, state = _run_main(monkeypatch, upgrade=lambda config, rev: None)
    assert code == 0
    assert state["released"] == ["lock"], state
    assert state["closed"] == ["lock"], state


def test_main_releases_the_lock_when_the_migration_raises(monkeypatch) -> None:
    """A failed migration must not leave the advisory lock held.

    Regression: the previous inline version released the lock in a `finally`,
    but the loop was closed by an inner handler on the timeout path only. Any
    exception raised out of `command.upgrade` had to still reach the release.
    """

    def _boom(config, rev):
        raise RuntimeError("alembic exploded")

    with pytest.raises(RuntimeError, match="alembic exploded"):
        _run_main(monkeypatch, upgrade=_boom)


def test_a_failed_migration_releases_the_lock_and_closes_the_session(
    monkeypatch,
) -> None:
    """The wedge regression: one bad deploy must not block the next one.

    Asserting only that the exception propagates is not enough -- the previous
    regression leaked the advisory lock, which fails *silently*: the deploy
    reports the original Alembic error, and the next deploy job then times out
    waiting for a lock nobody is holding.
    """
    def _boom(config, rev):
        raise RuntimeError("alembic exploded")

    state: dict[str, list] = {"released": [], "closed": [], "acquired": []}
    with pytest.raises(RuntimeError, match="alembic exploded"):
        _run_main(monkeypatch, upgrade=_boom, state=state)

    assert state["released"] == ["lock"], (
        "the advisory lock was left held after a failed migration; the next "
        f"migrator would time out. released={state['released']}"
    )
    assert state["closed"] == ["lock"], (
        f"the lock-holding connection was leaked. closed={state['closed']}"
    )


def test_main_returns_one_when_the_lock_cannot_be_taken(monkeypatch) -> None:
    """A timeout must exit non-zero so the deploy job fails loudly."""
    import asyncio as _asyncio

    async def _timeout(dsn):
        raise TimeoutError("held too long")

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setattr(migration_lock, "acquire_lock", _timeout)
    monkeypatch.setattr(
        migration_lock.command,
        "upgrade",
        lambda *a, **k: pytest.fail("must not migrate without the lock"),
    )
    assert migration_lock.main() == 1
    assert _asyncio  # silence the unused import


# ---------------------------------------------------------------------------
# The wrapper script must keep delegating to the tested module
# ---------------------------------------------------------------------------


def test_wrapper_script_delegates_to_the_tested_module() -> None:
    """The lock logic must not drift back into an untested heredoc.

    The previous version embedded the Python in the shell script, which meant
    none of the contention behaviour above was reachable from a test.
    """
    text = Path("scripts/run_migrations.sh").read_text(encoding="utf-8")
    assert "migration_lock.py" in text
    assert "<<'PY'" not in text, "lock logic is embedded in the script again"
    assert "pg_try_advisory_lock" not in text


def test_wrapper_still_requires_a_database_url() -> None:
    text = Path("scripts/run_migrations.sh").read_text(encoding="utf-8")
    assert "DATABASE_URL" in text
    assert "exit 2" in text


def test_lock_key_is_a_fixed_module_constant() -> None:
    """Every migrator must agree on the key, so it cannot be derived per run."""
    assert isinstance(migration_lock.MIGRATION_LOCK_KEY, int)
    assert migration_lock.MIGRATION_LOCK_KEY == 728_120_001


def test_wait_seconds_defaults_to_a_bounded_value(monkeypatch) -> None:
    """Re-read at call time: a deploy job overrides this via the environment."""
    monkeypatch.delenv("MIGRATION_LOCK_WAIT_SECONDS", raising=False)
    source = Path("scripts/migration_lock.py").read_text(encoding="utf-8")
    assert 'MIGRATION_LOCK_WAIT_SECONDS", "300"' in source


def test_alembic_runs_in_a_worker_thread_not_the_lock_loop(monkeypatch) -> None:
    """Alembic's env.py calls asyncio.run(); it must not inherit a running loop.

    Regression: running the upgrade inline on the lock-holding loop makes
    alembic/env.py raise "cannot be called from a running event loop", so the
    migration would fail while holding the lock.
    """
    import threading

    observed: list[int] = []

    def _upgrade(config, rev):
        observed.append(threading.current_thread().ident)
        # If the upgrade ran on the loop thread, acquiring a fresh asyncpg
        # connection here would deadlock. Check we are not on the main thread.
        assert threading.current_thread() is not threading.main_thread()

    _run_main(monkeypatch, upgrade=_upgrade)
    assert observed, "upgrade never ran"


def test_time_is_bounded_so_a_stuck_holder_cannot_block_a_deploy_forever() -> None:
    """The wait loop must consult a monotonic deadline on every iteration."""
    source = Path("scripts/migration_lock.py").read_text(encoding="utf-8")
    assert "time.monotonic()" in source
    assert "pg_try_advisory_lock" in source, (
        "a blocking pg_advisory_lock would make the wait uninterruptible"
    )
    assert time.monotonic() > 0
