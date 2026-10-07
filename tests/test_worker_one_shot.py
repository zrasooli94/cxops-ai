"""The scheduled one-shot worker must be bounded, silent, and semantically identical.

The pilot's background worker is a GitHub Actions run that starts, drains the
queue, and exits. That lifecycle creates four distinct ways to get it wrong, and
each is pinned below:

* **A one-shot that waits.** Sleeping for the next job turns a five-minute
  scheduled drain into an occupied runner, and the next scheduled run is the
  retry mechanism -- not the sleep. The test that forbids ``asyncio.sleep``
  outright is the cheapest guard against reintroducing the infinite loop's
  pacing into a process whose whole point is that it terminates.

* **A one-shot that serves metrics.** A Prometheus socket in a process that
  lives for seconds is unscrapeable for its entire life, and its port is a
  collision waiting for the next run. Only the long-lived mode may bind it.

* **Two copies of the job body.** Completion, retry, and failure semantics live
  in one function precisely so the two execution modes cannot drift. A test
  that exercises each terminal outcome through the shared function is what
  makes "shared" a property rather than a name.

* **A bound that cancels work.** ``max_seconds`` is checked *between* jobs. A
  job already claimed is finished even if the deadline passes mid-execution,
  because abandoning a ``processing`` row strands it in the stale-recovery
  window. The deadline test asserts the claim count; the finish test asserts
  the run's overrun is accepted.

Everything here runs without a network, a database, or the metrics port: the
repository, service, session factory, and scanner are replaced at the module
boundary, so nothing in this file can reach Neon even by accident.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import worker

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER_SOURCE = REPO_ROOT / "scripts" / "worker.py"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSession:
    """Stands in for the AsyncSession the claim and the job body share."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class _SessionContext:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class SessionFactory:
    """Callable factory so ``async with AsyncSessionLocal() as db`` works."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.calls = 0

    def __call__(self) -> _SessionContext:
        self.calls += 1
        return _SessionContext(self.session)


class ClaimSource:
    """``claim_next_unscoped`` replacement driven by a plain callable.

    The callable receives the claim number (1-based) and returns a job or
    ``None``, which keeps "always claimable" and "two jobs then empty" as one
    line each in the test that needs it.
    """

    def __init__(self, next_job: Any) -> None:
        self._next_job = next_job
        self.calls = 0

    async def claim(self, _db: FakeSession) -> Any:
        self.calls += 1
        return self._next_job(self.calls)


class LogRecorder:
    """Captures structured log events so assertions can be made on fields."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **fields: Any) -> None:
        self.events.append((level, event, fields))

    def debug(self, event: str, **fields: Any) -> None:
        self._record("debug", event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._record("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._record("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._record("error", event, **fields)

    def exception(self, event: str, **fields: Any) -> None:
        self._record("exception", event, **fields)

    def of(self, event: str) -> list[dict[str, Any]]:
        return [fields for _level, name, fields in self.events if name == event]


class Clock:
    """Deterministic ``time`` stand-in for the wall-clock deadline tests."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def make_job(
    job_id: int,
    *,
    job_type: str = "integration.test",
    status: str = "processing",
    payload: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        job_type=job_type,
        attempts=1,
        status=status,
        payload={"request_id": "req-1"} if payload is None else payload,
        last_error=None,
        organization_id=1,
    )


def install_queue(
    monkeypatch: pytest.MonkeyPatch,
    next_job: Any,
    *,
    keep_shared_body: bool = False,
) -> tuple[ClaimSource, SessionFactory, list[Any]]:
    """Wire a fake queue into the worker.

    ``keep_shared_body=True`` leaves the real ``process_claimed_job`` in place,
    so a test can prove the loop under test calls the shared body rather than a
    private copy of it. Everything else is faked: no database, no service, no
    scanner.
    """
    session = FakeSession()
    claim = ClaimSource(next_job)
    factory = SessionFactory(session)
    processed: list[Any] = []

    async def _record_process(_db: FakeSession, job: Any) -> None:
        processed.append(job)

    monkeypatch.setattr(worker, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker.IntegrationJobRepository, "claim_next_unscoped", claim.claim)
    if not keep_shared_body:
        monkeypatch.setattr(worker, "process_claimed_job", _record_process)
    monkeypatch.setattr(worker, "log", LogRecorder())

    async def _no_scan() -> None:
        return None

    monkeypatch.setattr(worker, "run_sla_scan_cycle", _no_scan)
    return claim, factory, processed


class StopLoop(Exception):
    """Raised from a fake ``asyncio.sleep`` to break the infinite loop."""


async def _forbidden_sleep(_seconds: float) -> None:
    raise AssertionError("one-shot mode must never sleep waiting for jobs")


# ---------------------------------------------------------------------------
# Normal (infinite) mode is unchanged
# ---------------------------------------------------------------------------


def test_normal_mode_starts_the_metrics_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    """The long-running worker keeps its Prometheus socket.

    The one-shot rule is "never bind", which is only safe if the process that
    stays up still does -- otherwise the incident question "is the worker
    alive?" loses its answer exactly when the pilot upgrades back to a
    continuous worker.
    """
    bound: list[int] = []

    async def _run_worker() -> None:
        return None

    monkeypatch.setattr(worker, "run_worker", _run_worker)
    monkeypatch.setattr(worker, "resolve_metrics_port", lambda: 9101)
    monkeypatch.setattr(worker, "start_http_server", bound.append)

    assert worker.main([]) == 0
    assert bound == [9101]


def test_normal_mode_still_waits_when_the_queue_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty queue -> sleep one second -> claim again. Never an exit.

    The infinite worker's contract is that it does not terminate, so the test
    has to interrupt it: the fake sleep raises after recording, and the fact
    that the exception escapes proves the loop had no other way out.
    """
    claim, _factory, _processed = install_queue(monkeypatch, lambda _n: None)
    slept: list[float] = []

    async def _sleep_then_stop(seconds: float) -> None:
        slept.append(seconds)
        raise StopLoop

    monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=_sleep_then_stop))

    with pytest.raises(StopLoop):
        asyncio.run(worker.run_worker())

    assert slept == [1]
    assert claim.calls >= 1


def test_normal_mode_processes_through_the_shared_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The infinite loop calls the same body the one-shot loop calls."""
    claim, _factory, processed = install_queue(
        monkeypatch,
        lambda n: make_job(n) if n == 1 else None,
    )

    async def _sleep_then_stop(_seconds: float) -> None:
        raise StopLoop

    monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=_sleep_then_stop))

    with pytest.raises(StopLoop):
        asyncio.run(worker.run_worker())

    assert [job.id for job in processed] == [1]
    assert claim.calls == 2


# ---------------------------------------------------------------------------
# One-shot: never serves metrics
# ---------------------------------------------------------------------------


def test_once_never_starts_the_prometheus_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--once`` must not bind a port, and must not even ask which one.

    ``resolve_metrics_port`` is asserted as well as ``start_http_server``: a
    port that is resolved but not bound still reads an environment variable
    the scheduled runner has no reason to carry, and the simpler statement is
    that one-shot mode is entirely unaware of the listener.
    """
    touched: list[Any] = []

    async def _run_worker_once(**_kwargs: Any) -> dict[str, Any]:
        touched.append("once")
        return {"jobs_processed": 0, "exit_reason": "queue_empty", "elapsed_seconds": 0.0}

    def _resolve() -> int:
        touched.append("resolve")
        return 9101

    monkeypatch.setattr(worker, "run_worker_once", _run_worker_once)
    monkeypatch.setattr(worker, "resolve_metrics_port", _resolve)
    monkeypatch.setattr(worker, "start_http_server", touched.append)

    assert worker.main(["--once"]) == 0
    assert touched == ["once"]


# ---------------------------------------------------------------------------
# One-shot: one SLA scan, then drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_once_runs_exactly_one_sla_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """One scan per run: enough to escalate on time, bounded by the advisory lock."""
    # install_queue wires a no-op scan first; the counting stub is installed
    # afterwards so the count below is the one the run actually observes.
    install_queue(monkeypatch, lambda _n: None)
    scans: list[int] = []

    async def _scan() -> None:
        scans.append(1)

    monkeypatch.setattr(worker, "run_sla_scan_cycle", _scan)

    summary = await worker.run_worker_once(max_jobs=25, max_seconds=240)

    assert scans == [1]
    assert summary["exit_reason"] == "queue_empty"
    assert summary["jobs_processed"] == 0


@pytest.mark.asyncio
async def test_once_exits_immediately_on_an_empty_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No work is a successful run: one claim, no waiting, exit code zero."""
    claim, _factory, _processed = install_queue(monkeypatch, lambda _n: None)
    monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=_forbidden_sleep))

    summary = await worker.run_worker_once(max_jobs=25, max_seconds=240)

    assert claim.calls == 1, "an empty queue must be detected on the first claim"
    assert summary["exit_reason"] == "queue_empty"
    assert summary["jobs_processed"] == 0


@pytest.mark.asyncio
async def test_once_processes_every_available_job_then_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Jobs are drained in claim order until the queue reports empty."""
    claim, _factory, processed = install_queue(
        monkeypatch,
        lambda n: make_job(n) if n <= 3 else None,
    )
    monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=_forbidden_sleep))

    summary = await worker.run_worker_once(max_jobs=25, max_seconds=240)

    assert [job.id for job in processed] == [1, 2, 3]
    assert claim.calls == 4, "the fourth claim is the one that proves the queue is empty"
    assert summary["exit_reason"] == "queue_empty"
    assert summary["jobs_processed"] == 3


# ---------------------------------------------------------------------------
# One-shot: the two bounds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_jobs_stops_further_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job bound is checked before claiming, so it is never overshot."""
    claim, _factory, processed = install_queue(
        monkeypatch,
        lambda n: make_job(n),  # the queue is always non-empty
    )
    monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=_forbidden_sleep))

    summary = await worker.run_worker_once(max_jobs=3, max_seconds=240)

    assert claim.calls == 3
    assert len(processed) == 3
    assert summary["exit_reason"] == "max_jobs"
    assert summary["jobs_processed"] == 3


@pytest.mark.asyncio
async def test_max_seconds_prevents_another_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deadline is checked before the next claim, not after it."""
    clock = Clock(now=1_000.0)
    monkeypatch.setattr(worker, "time", clock)

    claim, _factory, processed = install_queue(
        monkeypatch,
        lambda n: make_job(n),
    )

    async def _slow_job(_db: FakeSession, job: SimpleNamespace) -> None:
        processed.append(job)
        clock.now += 6  # this single job overruns the 5-second deadline

    monkeypatch.setattr(worker, "process_claimed_job", _slow_job)
    monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=_forbidden_sleep))

    summary = await worker.run_worker_once(max_jobs=25, max_seconds=5)

    assert claim.calls == 1, "the deadline must prevent the second claim"
    assert summary["exit_reason"] == "max_seconds"
    assert summary["jobs_processed"] == 1


@pytest.mark.asyncio
async def test_a_job_already_claimed_is_allowed_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing the deadline mid-job must not abandon a claimed row.

    Cancellation here would strand the job in ``processing`` until the
    stale-recovery window recycles it, which is a worse outcome than a run
    that overruns by the length of one job. The run reports ``max_seconds``
    only after the job it already owned has completed.
    """
    clock = Clock(now=1_000.0)
    monkeypatch.setattr(worker, "time", clock)
    install_queue(monkeypatch, lambda n: make_job(n))

    stages: list[str] = []

    async def _job_that_crosses_the_deadline(
        _db: FakeSession, _job: SimpleNamespace
    ) -> None:
        stages.append("started")
        clock.now += 60
        stages.append("finished")

    monkeypatch.setattr(worker, "process_claimed_job", _job_that_crosses_the_deadline)
    monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=_forbidden_sleep))

    summary = await worker.run_worker_once(max_jobs=25, max_seconds=5)

    assert stages == ["started", "finished"], "an in-flight job must run to completion"
    assert summary["exit_reason"] == "max_seconds"
    assert summary["jobs_processed"] == 1


@pytest.mark.asyncio
async def test_once_summary_logs_only_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counts, a reason, and a duration. No payload, no URL, no credential."""
    install_queue(monkeypatch, lambda n: make_job(n) if n == 1 else None)
    recorder = LogRecorder()
    monkeypatch.setattr(worker, "log", recorder)

    summary = await worker.run_worker_once(max_jobs=25, max_seconds=240)

    completed = recorder.of("worker_once_completed")
    assert len(completed) == 1
    assert set(completed[0]) == {"jobs_processed", "exit_reason", "elapsed_seconds"}
    assert summary["exit_reason"] == "queue_empty"

    forbidden = ("postgres://", "postgresql://", "ENCRYPTION_KEYS", "Bearer ", "sk-")
    for _level, event, fields in recorder.events:
        for key, value in fields.items():
            rendered = f"{key}={value}"
            assert not any(token in rendered for token in forbidden), (
                f"{event} logged secret-shaped material: {rendered!r}"
            )


# ---------------------------------------------------------------------------
# Shared processing keeps the terminal semantics
# ---------------------------------------------------------------------------


def _install_process_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    execute: Any,
    fresh_status: str | None = "processing",
    failed_status: str | None = None,
) -> dict[str, list[Any]]:
    """Replace the collaborators ``process_claimed_job`` reaches for."""
    seen: dict[str, list[Any]] = {
        "executed": [],
        "completed": [],
        "failed": [],
        "retried": [],
        "handle_failure": [],
        "completed_metric": [],
        "failure_metric": [],
        "retry_metric": [],
    }

    async def _execute(*, db: FakeSession, job: SimpleNamespace) -> None:
        seen["executed"].append(job.id)
        # Positional on purpose: the canned fakes below are written in the
        # same shape the real ``execute`` is, and keyword names would be a
        # second thing to keep in sync for no benefit.
        await execute(db, job)

    async def _get_by_id(*, db: FakeSession, job_id: int) -> Any:
        if fresh_status is None:
            return None
        return SimpleNamespace(status=fresh_status, last_error=None)

    async def _mark_completed(*, db: FakeSession, job_id: int) -> None:
        seen["completed"].append(job_id)

    async def _mark_failed(*, db: FakeSession, job_id: int, error_message: str) -> Any:
        seen["failed"].append((job_id, error_message))
        return SimpleNamespace(status=failed_status, id=job_id) if failed_status else None

    async def _handle_failure(*, db: FakeSession, job: Any, error_message: str) -> None:
        seen["handle_failure"].append(error_message)

    monkeypatch.setattr(worker.IntegrationJobService, "execute", _execute)
    monkeypatch.setattr(worker.IntegrationJobRepository, "get_by_id_unscoped", _get_by_id)
    monkeypatch.setattr(worker.IntegrationJobRepository, "mark_completed", _mark_completed)
    monkeypatch.setattr(worker.IntegrationJobRepository, "mark_failed", _mark_failed)
    monkeypatch.setattr(worker.IntegrationJobService, "handle_failure", _handle_failure)
    monkeypatch.setattr(
        worker,
        "record_integration_job_completed",
        lambda job_type: seen["completed_metric"].append(job_type),
    )
    monkeypatch.setattr(
        worker,
        "record_integration_job_failure",
        lambda job_type: seen["failure_metric"].append(job_type),
    )
    monkeypatch.setattr(
        worker,
        "record_integration_job_retry",
        lambda job_type: seen["retry_metric"].append(job_type),
    )
    monkeypatch.setattr(worker, "log", LogRecorder())
    return seen


async def _ok(_db: FakeSession, _job: SimpleNamespace) -> None:
    return None


async def _boom(_db: FakeSession, _job: SimpleNamespace) -> None:
    raise RuntimeError("provider rejected the job")


@pytest.mark.asyncio
async def test_shared_processing_marks_a_processing_job_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path still completes and still records the metric."""
    seen = _install_process_fakes(monkeypatch, execute=_ok, fresh_status="processing")

    await worker.process_claimed_job(FakeSession(), make_job(7))

    assert seen["executed"] == [7]
    assert seen["completed"] == [7]
    assert seen["completed_metric"] == ["integration.test"]
    assert seen["failed"] == [] and seen["retried"] == []


@pytest.mark.asyncio
async def test_shared_processing_leaves_a_job_execute_already_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-retryable failure marked by ``execute`` is never completed.

    This is the branch that stops the worker from overwriting a terminal
    ``failed`` row with ``completed`` -- re-completing it would hide the
    failure from every downstream report.
    """
    seen = _install_process_fakes(monkeypatch, execute=_ok, fresh_status="failed")

    await worker.process_claimed_job(FakeSession(), make_job(7))

    assert seen["completed"] == []
    assert seen["failure_metric"] == ["integration.test"]


@pytest.mark.asyncio
async def test_shared_processing_schedules_a_retry_when_execute_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception still rolls back, marks retry, and records the retry."""
    session = FakeSession()
    seen = _install_process_fakes(
        monkeypatch,
        execute=_boom,
        fresh_status=None,
        failed_status="retry",
    )

    await worker.process_claimed_job(session, make_job(7))

    assert session.rollbacks == 1, "the partial work must be rolled back first"
    assert seen["failed"] and seen["failed"][0][0] == 7
    assert seen["handle_failure"] == ["provider rejected the job"]
    assert seen["retry_metric"] == ["integration.test"]
    assert seen["completed_metric"] == []


@pytest.mark.asyncio
async def test_shared_processing_records_a_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-retryable exception is recorded as a failure, not a completion."""
    session = FakeSession()
    seen = _install_process_fakes(
        monkeypatch,
        execute=_boom,
        fresh_status=None,
        failed_status="failed",
    )

    await worker.process_claimed_job(session, make_job(7))

    assert session.rollbacks == 1
    assert seen["failure_metric"] == ["integration.test"]
    assert seen["completed_metric"] == []
    assert seen["retry_metric"] == []


@pytest.mark.asyncio
async def test_once_uses_the_same_shared_processing_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the one-shot loop routes through ``process_claimed_job``.

    The completion assertions above prove the body's behaviour in isolation;
    this proves the bounded loop actually calls *that* body instead of a
    private copy, which is the whole point of the refactor. The shared body is
    left real here and only its collaborators are faked, so a future
    `run_worker_once` that grew its own inline copy would fail this test with
    ``executed == []`` while still passing every bound test.
    """
    seen = _install_process_fakes(monkeypatch, execute=_ok, fresh_status="processing")
    claim, _factory, _processed = install_queue(
        monkeypatch,
        lambda n: make_job(n) if n <= 2 else None,
        keep_shared_body=True,
    )
    monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=_forbidden_sleep))

    summary = await worker.run_worker_once(max_jobs=25, max_seconds=240)

    assert seen["executed"] == [1, 2], "the shared body must be what the loop calls"
    assert seen["completed"] == [1, 2]
    assert seen["completed_metric"] == ["integration.test", "integration.test"]
    assert claim.calls == 3
    assert summary["jobs_processed"] == 2
    assert summary["exit_reason"] == "queue_empty"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_defaults_are_the_scheduled_bounds() -> None:
    """Defaults are the values the workflow passes explicitly."""
    args = worker.parse_args(["--once"])
    assert args.once is True
    assert args.max_jobs == 25
    assert args.max_seconds == 240

    defaults = worker.parse_args([])
    assert defaults.once is False
    assert defaults.max_jobs == 25
    assert defaults.max_seconds == 240


@pytest.mark.parametrize(
    "argv",
    [
        ["--once", "--max-jobs", "0"],
        ["--once", "--max-jobs", "-3"],
        ["--max-jobs", "0"],
    ],
    ids=["zero", "negative", "even-outside-once"],
)
def test_invalid_max_jobs_fails_before_starting(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """A bound of zero would report success while doing nothing at all."""
    started: list[Any] = []
    monkeypatch.setattr(worker, "start_http_server", started.append)

    async def _should_not_run(**_kwargs: Any) -> dict[str, Any]:
        started.append("worker")
        return {}

    monkeypatch.setattr(worker, "run_worker_once", _should_not_run)

    with pytest.raises(SystemExit) as excinfo:
        worker.main(argv)

    assert excinfo.value.code == 2
    assert started == [], "invalid bounds must fail before anything starts"
    assert "--max-jobs must be >= 1" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--once", "--max-seconds", "0"],
        ["--once", "--max-seconds", "-0.5"],
        ["--once", "--max-seconds", "soon"],
    ],
    ids=["zero", "negative", "unparseable"],
)
def test_invalid_max_seconds_fails_before_starting(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """A deadline of zero would exit before claiming, silently doing nothing."""
    started: list[Any] = []
    monkeypatch.setattr(worker, "start_http_server", started.append)

    async def _should_not_run(**_kwargs: Any) -> dict[str, Any]:
        started.append("worker")
        return {}

    monkeypatch.setattr(worker, "run_worker_once", _should_not_run)

    with pytest.raises(SystemExit) as excinfo:
        worker.main(argv)

    assert excinfo.value.code == 2
    assert started == []


# ---------------------------------------------------------------------------
# No migrations, anywhere in this module
# ---------------------------------------------------------------------------


def test_the_worker_module_never_invokes_migrations() -> None:
    """Migrations are an explicit operator step, not a worker behaviour.

    A scheduled job is exactly where an ``alembic upgrade`` would look
    convenient, and exactly where it is most dangerous: every five minutes a
    second migrator would contend for the advisory lock against a deploy that
    an operator is running by hand.
    """
    source = WORKER_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("run_migrations", "alembic", "upgrade head", "subprocess"):
        assert forbidden not in source, f"scripts/worker.py references {forbidden!r}"
