"""Actual CXOps worker with 160 MiB simulated provider buffers; no providers.

Stdlib-only dispatcher validates exact disposable GitHub Actions environment
BEFORE any application import. All database writes are local and synthetic.
"""
from __future__ import annotations

import asyncio
import json
import os
import resource
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from scripts.worker_disposable_guard import require_disposable_environment

MARKER = "cxops_disposable_memory_probe_v1"
JOB_TYPE = "synthetic.worker.memory"
ALLOC_MIB = 160
RSS_CEILING_MIB = 384
JOBS = 6


def rss_mib(kind: int) -> float:
    value = resource.getrusage(kind).ru_maxrss
    if sys.platform == "darwin":
        return round(value / (1024 * 1024), 3)
    return round(value / 1024, 3)


async def synthetic_run() -> dict[str, object]:
    # Application and driver imports come AFTER the disposable guard.
    from sqlalchemy import delete, func, select

    from app.core.database import AsyncSessionLocal
    from app.models.integration_job import IntegrationJob
    from app.models.organization import Organization
    from scripts import worker

    async with AsyncSessionLocal() as db:
        for model in (Organization, IntegrationJob):
            count = (
                await db.execute(select(func.count()).select_from(model))
            ).scalar_one()
            if count != 0:
                raise RuntimeError("Disposable stress database must start empty")

    name = "cxops-mem-" + uuid.uuid4().hex
    org_id: int | None = None
    job_ids: list[int] = []
    observed: list[int] = []
    try:
        async with AsyncSessionLocal() as db:
            org = Organization(name=name)
            db.add(org)
            await db.flush()
            org_id = org.id
            jobs = [
                IntegrationJob(
                    dedupe_key=f"{name}:{i}",
                    organization_id=org_id,
                    job_type=JOB_TYPE,
                    payload={"synthetic": True, "index": i},
                    status="pending",
                    attempts=0,
                    max_attempts=3,
                    available_at=datetime.now(UTC) - timedelta(seconds=5),
                )
                for i in range(JOBS)
            ]
            db.add_all(jobs)
            await db.flush()
            job_ids = [job.id for job in jobs]
            await db.commit()

        async def fake_provider(*, db: object, job: IntegrationJob) -> None:
            assert job.id in job_ids
            assert job.organization_id == org_id
            assert job.job_type == JOB_TYPE
            assert job.payload.get("synthetic") is True
            # Touch each page to force physical commitment, not virtual memory.
            payload = bytearray(ALLOC_MIB * 1024 * 1024)
            for i in range(0, len(payload), 4096):
                payload[i] = 9
            observed.append(job.id)
            await asyncio.sleep(0.05)
            assert payload[0] == 9

        async def forbid_external_failure(**_kwargs: object) -> None:
            raise AssertionError("No external failure handlers allowed")

        worker.IntegrationJobService.execute = staticmethod(fake_provider)
        worker.IntegrationJobService.handle_failure = staticmethod(
            forbid_external_failure
        )
        # Real one-shot SQL claims, SLA scan and empty chat-expiry pass.
        result = await worker.run_worker_once(
            max_jobs=25,
            max_seconds=60,
            finalize_expired_public_chat=True,
            expiry_batch_size=50,
        )
        async with AsyncSessionLocal() as db:
            rows = list(
                (
                    await db.execute(
                        select(IntegrationJob)
                        .where(IntegrationJob.id.in_(job_ids))
                        .order_by(IntegrationJob.id)
                    )
                ).scalars().all()
            )
        success = (
            result["jobs_processed"] == JOBS
            and result["health"] == worker.RUN_HEALTHY
            and result.get("expiry_outcome") == "ok"
            and len(set(observed)) == JOBS
            and set(observed) == set(job_ids)
            and all(row.status == "completed" and row.attempts == 1 for row in rows)
        )
        return {
            "scenario": MARKER,
            "pass": success,
            "jobs_completed": len(observed),
            "simulated_buffer_mib": ALLOC_MIB,
            "actual_provider_calls": 0,
            "real_provider_memory_verified": False,
            "self_rss_mib": rss_mib(resource.RUSAGE_SELF),
            "worker_elapsed_s": round(float(result["elapsed_seconds"]), 3),
        }
    finally:
        if org_id is not None:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(IntegrationJob).where(
                        IntegrationJob.organization_id == org_id,
                        IntegrationJob.job_type == JOB_TYPE,
                    )
                )
                await db.execute(delete(Organization).where(Organization.id == org_id))
                await db.commit()


def isolated_child_env(url: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DATABASE_URL": url,
        "ENVIRONMENT": "development",
        "DEBUG": "false",
        "AUTH_MODE": "hs256",
        "AUTH_DEV_MODE": "false",
        "GITHUB_ACTIONS": "true",
        "CXOPS_WORKER_DISPOSABLE_PROBE": "true",
        "OPENAI_API_KEY": "",
        "PYTHONUNBUFFERED": "1",
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        url = require_disposable_environment(os.environ)
    except ValueError as exc:
        print("Unsafe stress environment: " + type(exc).__name__, file=sys.stderr)
        return 2
    args = list(argv if argv is not None else sys.argv[1:])
    if args == ["--child"]:
        try:
            result = asyncio.run(synthetic_run())
        except Exception as exc:
            print("Synthetic worker error: " + type(exc).__name__, file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0 if result["pass"] else 1
    if args:
        print("Unsupported simulation options", file=sys.stderr)
        return 2

    start = time.monotonic()
    try:
        child = subprocess.run(
            [sys.executable, "-m", "scripts.worker_disposable_memory_probe", "--child"],
            env=isolated_child_env(url),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("Bounded simulation timed out", file=sys.stderr)
        return 1

    # Never print application logs; report only whitelisted synthetic fields.
    report = None
    for line in child.stdout.splitlines():
        if '"scenario": "' + MARKER + '"' in line:
            try:
                report = json.loads(line)
            except json.JSONDecodeError:
                report = None
    peak = rss_mib(resource.RUSAGE_CHILDREN)
    passed = (
        child.returncode == 0
        and isinstance(report, dict)
        and report.get("scenario") == MARKER
        and report.get("pass") is True
        and report.get("jobs_completed") == JOBS
        and report.get("actual_provider_calls") == 0
        and report.get("real_provider_memory_verified") is False
        and report.get("simulated_buffer_mib") == ALLOC_MIB
        and ALLOC_MIB < peak < RSS_CEILING_MIB
    )
    print(json.dumps({
        "probe": MARKER,
        "pass": passed,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "child_peak_rss_mib": peak,
        "rss_ceiling_mib": RSS_CEILING_MIB,
        "simulated_buffer_mib": ALLOC_MIB,
        "jobs_completed": report.get("jobs_completed") if report else None,
        "real_provider_calls": 0,
        "real_provider_load_certified": False,
        "child_exit_code": child.returncode,
    }, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
