"""Disposable PostgreSQL process-SIGTERM recovery probe.

Runs only in GitHub Actions against the exact loopback synthetic database.
A separate Python worker process is terminated while its fake provider is
blocked, then the ordinary worker reclaims the job after test-local aging.
No Render, live provider, production credentials, or customer data are used.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.worker_disposable_guard import require_disposable_environment
from scripts.worker_disposable_memory_probe import isolated_child_env

MARKER = "cxops_disposable_sigterm_probe_v1"
JOB_TYPE = "synthetic.worker.sigterm"
WAIT_SECONDS = 12


def check(value: bool, reason: str) -> None:
    if not value:
        raise RuntimeError(reason)


async def _hold_child() -> None:
    # Invoked in its own process only after the exact local environment gate.
    from app.models.integration_job import IntegrationJob
    from scripts import worker

    async def blocked_provider(*, db: object, job: IntegrationJob) -> None:
        check(job.job_type == JOB_TYPE, "unexpected job type in child")
        check(job.payload.get("synthetic") is True, "non-synthetic child job")
        marker = Path(os.environ["CXOPS_DISPOSABLE_BLOCK_MARKER"])
        marker.write_text("blocked", encoding="utf-8")
        await asyncio.Event().wait()  # intentionally terminated by parent

    worker.IntegrationJobService.execute = staticmethod(blocked_provider)
    await worker.run_worker_once(max_jobs=1, max_seconds=30)


async def _parent() -> dict[str, object]:
    # All app imports follow an independent preflight in main().
    from sqlalchemy import delete, func, select, update

    from app.core.database import AsyncSessionLocal
    from app.models.integration_job import IntegrationJob
    from app.models.organization import Organization
    from scripts import worker

    async with AsyncSessionLocal() as db:
        for model in (Organization, IntegrationJob):
            count = (await db.execute(select(func.count()).select_from(model))).scalar_one()
            check(count == 0, "disposable database must start empty")

    org_id: int | None = None
    job_id: int | None = None
    child: asyncio.subprocess.Process | None = None
    completed_by_recovery: list[int] = []
    try:
        unique = uuid.uuid4().hex
        async with AsyncSessionLocal() as db:
            org = Organization(name="cxops-sigterm-" + unique)
            db.add(org)
            await db.flush()
            org_id = org.id
            job = IntegrationJob(
                dedupe_key="cxops-sigterm:" + unique,
                job_type=JOB_TYPE,
                organization_id=org_id,
                payload={"synthetic": True},
                status="pending",
                attempts=0,
                max_attempts=3,
                available_at=datetime.now(UTC) - timedelta(seconds=5),
            )
            db.add(job)
            await db.flush()
            job_id = job.id
            await db.commit()

        async def get_row() -> IntegrationJob:
            async with AsyncSessionLocal() as db:
                record = await db.scalar(
                    select(IntegrationJob).where(IntegrationJob.id == job_id)
                )
                check(record is not None, "synthetic job missing")
                return record

        with tempfile.TemporaryDirectory(prefix="cxops-sigterm-disposable-") as temp:
            marker_path = Path(temp) / "fake-provider-entered"
            child_env = isolated_child_env(require_disposable_environment(os.environ))
            child_env["CXOPS_DISPOSABLE_BLOCK_MARKER"] = str(marker_path)
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "scripts.worker_disposable_termination_probe",
                "--hold-child",
                env=child_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            until = time.monotonic() + WAIT_SECONDS
            while not marker_path.exists():
                check(child.returncode is None, "child exited before fake provider block")
                check(time.monotonic() < until, "child did not enter fake provider")
                await asyncio.sleep(0.05)
            check(
                marker_path.read_text(encoding="utf-8") == "blocked",
                "fake provider marker incorrect",
            )
            claimed = await get_row()
            check(
                claimed.status == "processing" and claimed.attempts == 1
                and claimed.locked_at is not None and claimed.completed_at is None,
                "job was not durably claimed before SIGTERM",
            )

            child.terminate()
            try:
                child_code = await asyncio.wait_for(child.wait(), timeout=6)
            except TimeoutError as exc:
                child.kill()
                await asyncio.wait_for(child.wait(), timeout=6)
                raise RuntimeError("child resisted SIGTERM") from exc
            check(child_code == -signal.SIGTERM, "unexpected child termination status")

        after_kill = await get_row()
        check(
            after_kill.status == "processing" and after_kill.attempts == 1
            and after_kill.locked_at is not None and after_kill.completed_at is None,
            "terminated process falsely completed or released its job",
        )

        async def recovered_fake(*, db: object, job: IntegrationJob) -> None:
            check(job.id == job_id, "wrong job was recovered")
            check(job.job_type == JOB_TYPE, "wrong job type in recovery")
            check(job.organization_id == org_id, "wrong tenant in recovery")
            check(job.payload.get("synthetic") is True, "non-synthetic recovery")
            completed_by_recovery.append(job.id)

        worker.IntegrationJobService.execute = staticmethod(recovered_fake)
        early = await worker.run_worker_once(max_jobs=1, max_seconds=10)
        check(early["jobs_processed"] == 0, "claimed job stolen before stale threshold")
        check((await get_row()).status == "processing", "pre-stale job state changed")

        # Only this disposable synthetic row is artificially aged; production
        # retry intervals and stale thresholds remain entirely unmodified.
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(IntegrationJob)
                .where(IntegrationJob.id == job_id, IntegrationJob.status == "processing")
                .values(locked_at=datetime.now(UTC) - timedelta(minutes=15))
            )
            await db.commit()

        recovered = await worker.run_worker_once(max_jobs=1, max_seconds=10)
        final = await get_row()
        check(recovered["jobs_processed"] == 1, "stale job was not reclaimed")
        check(recovered["health"] == worker.RUN_HEALTHY, "recovery worker unhealthy")
        check(completed_by_recovery == [job_id], "duplicate/missing fake execution")
        check(
            final.status == "completed" and final.attempts == 2
            and final.locked_at is None and final.completed_at is not None,
            "recovered job has invalid terminal state",
        )
        return {
            "probe": MARKER,
            "pass": True,
            "terminated_child_signal": "SIGTERM",
            "claim_persisted_after_process_exit": True,
            "pre_stale_reclaim_denied": True,
            "test_local_stale_age_minutes": 15,
            "recovered_attempts": final.attempts,
            "completed_once_by_recovery": True,
            "real_provider_operations": "replaced_with_inert_stub",
            "production_state_changed": False,
        }
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await asyncio.wait_for(child.wait(), timeout=6)
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


def main(argv: Sequence[str] | None = None) -> int:
    try:
        require_disposable_environment(os.environ)
    except ValueError:
        print("SIGTERM probe environment refused", file=sys.stderr)
        return 2

    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--hold-child"]:
        try:
            asyncio.run(_hold_child())
        except (AssertionError, OSError, RuntimeError, ValueError):
            return 1
        return 1  # child must not exit normally

    if args:
        print("Unsupported SIGTERM probe arguments", file=sys.stderr)
        return 2

    try:
        report = asyncio.run(_parent())
    except (AssertionError, OSError, RuntimeError, ValueError) as exc:
        # No raw provider logs, subprocess output, DB URL or secret value.
        print(json.dumps({"probe": MARKER, "pass": False, "error_type": type(exc).__name__,
                          "failure": str(exc) if isinstance(exc, RuntimeError) else "probe failure"},
                         sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
