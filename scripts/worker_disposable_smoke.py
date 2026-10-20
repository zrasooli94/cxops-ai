"""Execute the ACTUAL one-shot worker once on empty, disposable local PG.

Unlike the inert Cron canary this loads the real CXOps Python application and
its scheduled SLA/job/expiry path. It cannot authenticate to any provider and
requires a dedicated empty PostgreSQL database on 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import json
import os
import resource
import subprocess
import sys
import time
from collections.abc import Sequence

from scripts.worker_disposable_guard import (
    DISPOSABLE_DB,
    DISPOSABLE_HOST,
    DISPOSABLE_PASSWORD,
    DISPOSABLE_PORT,
    DISPOSABLE_USER,
    require_disposable_environment,
)

MAX_RUNTIME_SECONDS = 90
MAX_PROCESS_RSS_MIB = 384


async def _counts() -> dict[str, int]:
    # Import only after the exact local DB guard has succeeded.
    import asyncpg

    connection = await asyncpg.connect(
        host=DISPOSABLE_HOST,
        port=DISPOSABLE_PORT,
        database=DISPOSABLE_DB,
        user=DISPOSABLE_USER,
        password=DISPOSABLE_PASSWORD,
        timeout=5,
    )
    try:
        return {
            name: int(await connection.fetchval(f"SELECT count(*) FROM {table}"))
            for name, table in (
                ("integration_jobs", "integration_jobs"),
                ("organizations", "organizations"),
                ("tickets", "tickets"),
                ("public_chat_sessions", "public_chat_sessions"),
            )
        }
    finally:
        await connection.close()


def _isolated_child_env(url: str) -> dict[str, str]:
    # Never inherit a user's or CI runner's provider credentials into the
    # process that loads the real worker.
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DATABASE_URL": url,
        "ENVIRONMENT": "development",
        "DEBUG": "false",
        "AUTH_MODE": "hs256",
        "AUTH_DEV_MODE": "false",
        "OPENAI_API_KEY": "",
        "PYTHONUNBUFFERED": "1",
        "GITHUB_ACTIONS": "true",
        "CXOPS_WORKER_DISPOSABLE_PROBE": "true",
    }


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        print("No CLI arguments accepted for the disposable probe", file=sys.stderr)
        return 2
    try:
        url = require_disposable_environment(os.environ)
        before = asyncio.run(_counts())
    except (ValueError, OSError, RuntimeError) as exc:
        print("Probe preflight failed: " + type(exc).__name__, file=sys.stderr)
        return 2
    if any(before.values()):
        print("Probe refuses nonempty local database", file=sys.stderr)
        return 2

    command = [
        sys.executable,
        "-m",
        "scripts.worker",
        "--once",
        "--max-jobs",
        "25",
        "--max-seconds",
        "240",
        "--exit-nonzero-on-unsuccessful",
        "--finalize-expired-public-chat",
        "--expiry-batch-size",
        "50",
    ]
    start = time.monotonic()
    try:
        result = subprocess.run(
            command,
            env=_isolated_child_env(url),
            capture_output=True,
            text=True,
            check=False,
            timeout=MAX_RUNTIME_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print("Disposable real worker exceeded probe timeout", file=sys.stderr)
        return 1
    elapsed = time.monotonic() - start
    peak_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    try:
        after = asyncio.run(_counts())
    except (OSError, RuntimeError) as exc:
        print("Probe postflight failed: " + type(exc).__name__, file=sys.stderr)
        return 1
    # Never print captured application logs, even when the test fails.
    successful = (
        result.returncode == 0
        and before == after
        and "worker_once_summary" in result.stdout + result.stderr
        and "worker_once_completed" in result.stdout + result.stderr
        and "public_chat_expiry" in result.stdout + result.stderr
        and elapsed < MAX_RUNTIME_SECONDS
        and 0 < peak_rss < MAX_PROCESS_RSS_MIB
    )
    print(
        json.dumps(
            {
                "probe": "real_one_shot_empty_disposable_pg",
                "pass": successful,
                "worker_exit_code": result.returncode,
                "elapsed_seconds": round(elapsed, 3),
                "child_peak_rss_mib": round(peak_rss, 3),
                "jobs_before_and_after": before["integration_jobs"],
                "data_unchanged": before == after,
                "provider_credentials": "not_provided",
                "real_provider_load_test": False,
            },
            sort_keys=True,
        )
    )
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
