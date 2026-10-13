#!/usr/bin/env python3
"""Read-only audit of a scheduled GitHub Actions workflow's real execution.

A ``cron`` in a workflow file is a request, not a guarantee: GitHub documents
scheduled events as best-effort - they can be delayed under load and some may
be dropped. This tool measures what actually happened so the gap between the
configured schedule and observed executions is evidence, not anecdote.

    # live, read-only (uses the authenticated `gh` CLI, GET requests only)
    .venv/bin/python scripts/audit_workflow_schedule.py \\
        --repo zrasooli94/cxops-ai --workflow production-worker.yml \\
        --interval-minutes 5 --with-jobs --markdown

    # offline, from previously captured `gh api` output (JSON lines)
    .venv/bin/python scripts/audit_workflow_schedule.py \\
        --runs-file runs.jsonl --jobs-file jobs.jsonl --interval-minutes 5

What the GitHub API can and cannot tell us - and therefore what this reports:

* A run that never existed leaves no record. "Missing windows" are inferred
  from the gaps between scheduled runs on the cron grid; the API does not say
  WHY a trigger did not fire.
* A scheduled run's ``created_at`` is when GitHub created it, not which cron
  tick it served. ``trigger_offset`` (minutes past the most recent grid tick)
  is therefore a LOWER BOUND on trigger delay, not the delay itself.
* Queue delay is measured from run/job timestamps only where both exist.

It only ever issues ``gh api`` GET requests: no workflow is dispatched,
re-run, cancelled, enabled, or disabled.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

WORKER_STEP_NAME = "Run one-shot worker"

# Only these `gh` argument shapes are ever executed (see ``_gh_get``).
_FORBIDDEN_GH_ARGS = ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def floor_to_grid(moment: datetime, interval: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    steps = (moment - epoch) // interval
    return epoch + steps * interval


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; ``None`` for no data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(pct / 100 * len(ordered) + 0.5)))
    return ordered[rank - 1]


def _minutes(delta: timedelta) -> float:
    return round(delta.total_seconds() / 60, 2)


def _seconds(delta: timedelta) -> float:
    return round(delta.total_seconds(), 1)


@dataclass(frozen=True)
class AuditWindow:
    start: datetime
    end: datetime
    interval: timedelta


def analyze(
    runs: list[dict[str, Any]],
    jobs: list[dict[str, Any]] | None,
    *,
    interval_minutes: int,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    worker_step_name: str = WORKER_STEP_NAME,
    timeout_minutes: float | None = None,
) -> dict[str, Any]:
    """Pure analysis of workflow-run (and optional job) records."""
    interval = timedelta(minutes=interval_minutes)
    created: list[tuple[datetime, dict[str, Any]]] = []
    for run in runs:
        stamp = parse_ts(run.get("created_at"))
        if stamp is not None:
            created.append((stamp, run))
    created.sort(key=lambda item: item[0])
    scheduled = [(ts, r) for ts, r in created if r.get("event") == "schedule"]

    start = window_start or (created[0][0] if created else None)
    end = window_end or datetime.now(UTC)
    report: dict[str, Any] = {
        "interval_minutes": interval_minutes,
        "window_start": start.isoformat() if start else None,
        "window_end": end.isoformat(),
        "runs_total": len(runs),
        "runs_by_event": dict(Counter(r.get("event") for r in runs)),
        "runs_by_event_and_conclusion": {
            f"{event}:{conclusion}": count
            for (event, conclusion), count in sorted(
                Counter((r.get("event"), r.get("conclusion")) for r in runs).items(),
                key=lambda item: str(item[0]),
            )
        },
        "limitations": [
            (
                "Missing windows are inferred from gaps on the cron grid; the API does "
                "not record triggers that never fired, nor why."
            ),
            (
                "trigger_offset is minutes past the most recent cron tick: a lower bound "
                "on trigger delay, because a run does not record which tick it served."
            ),
        ],
    }
    if start is None:
        report["scheduled"] = {"count": 0}
        return report

    first_tick = floor_to_grid(start, interval)
    if first_tick < start:
        first_tick += interval
    expected = max(0, int((end - first_tick) // interval) + 1) if end >= first_tick else 0

    sched_times = [ts for ts, _ in scheduled]
    intervals = [_minutes(later - earlier) for earlier, later in itertools.pairwise(sched_times)]
    missing_between = [max(0, round(gap / interval_minutes) - 1) for gap in intervals]
    leading_gap = _minutes(sched_times[0] - first_tick) if sched_times else _minutes(end - first_tick)
    trailing_gap = _minutes(end - sched_times[-1]) if sched_times else None
    trailing_missing = (
        max(0, int((end - floor_to_grid(sched_times[-1], interval)) // interval))
        if sched_times
        else expected
    )
    offsets = [_minutes(ts - floor_to_grid(ts, interval)) for ts in sched_times]
    queue_delays = []
    for ts, run in scheduled:
        run_started = parse_ts(run.get("run_started_at"))
        if run_started is not None:
            queue_delays.append(_seconds(run_started - ts))

    longest_gap = max(intervals) if intervals else None
    longest_gap_between = None
    if intervals and longest_gap is not None:
        index = intervals.index(longest_gap)
        longest_gap_between = [sched_times[index].isoformat(), sched_times[index + 1].isoformat()]

    report["scheduled"] = {
        "expected_windows": expected,
        "observed_runs": len(scheduled),
        "coverage_pct": round(100 * len(scheduled) / expected, 2) if expected else None,
        "missing_windows_between_runs": sum(missing_between),
        "missing_windows_before_first_run": max(0, round(leading_gap / interval_minutes)),
        "missing_windows_after_last_run": trailing_missing,
        "longest_consecutive_missing_between_runs": max(missing_between) if missing_between else 0,
        "intervals_minutes": {
            "count": len(intervals),
            "median": statistics.median(intervals) if intervals else None,
            "p90": percentile(intervals, 90),
            "p95": percentile(intervals, 95),
            "max": longest_gap,
            "max_between": longest_gap_between,
        },
        "trailing_gap_minutes": trailing_gap,
        "last_scheduled_run": sched_times[-1].isoformat() if sched_times else None,
        "trigger_offset_minutes_lower_bound": {
            "median": statistics.median(offsets) if offsets else None,
            "max": max(offsets) if offsets else None,
        },
        "run_queue_delay_seconds": {
            "median": statistics.median(queue_delays) if queue_delays else None,
            "max": max(queue_delays) if queue_delays else None,
        },
        "hour_of_day_utc": dict(sorted(Counter(ts.hour for ts in sched_times).items())),
    }

    if jobs is not None:
        report["jobs"] = _analyze_jobs(
            jobs, worker_step_name=worker_step_name, timeout_minutes=timeout_minutes
        )
    return report


def _analyze_jobs(
    jobs: list[dict[str, Any]], *, worker_step_name: str, timeout_minutes: float | None
) -> dict[str, Any]:
    durations: list[float] = []
    job_queue: list[float] = []
    step_durations: list[float] = []
    for job in jobs:
        started, completed, created = (
            parse_ts(job.get("started_at")),
            parse_ts(job.get("completed_at")),
            parse_ts(job.get("created_at")),
        )
        if started and completed:
            durations.append(_seconds(completed - started))
        if created and started:
            job_queue.append(_seconds(started - created))
        for step in job.get("steps") or []:
            if step.get("name") == worker_step_name:
                s_start, s_end = parse_ts(step.get("started_at")), parse_ts(step.get("completed_at"))
                if s_start and s_end:
                    step_durations.append(_seconds(s_end - s_start))
    conclusions = Counter(job.get("conclusion") for job in jobs)
    near_timeout = (
        sum(1 for d in durations if d >= 0.9 * timeout_minutes * 60) if timeout_minutes else None
    )
    return {
        "count": len(jobs),
        "conclusions": dict(conclusions),
        "timed_out": conclusions.get("timed_out", 0),
        "cancelled": conclusions.get("cancelled", 0),
        "duration_seconds": {
            "median": statistics.median(durations) if durations else None,
            "p95": percentile(durations, 95),
            "max": max(durations) if durations else None,
        },
        "worker_step_seconds": {
            "median": statistics.median(step_durations) if step_durations else None,
            "max": max(step_durations) if step_durations else None,
        },
        "job_queue_seconds": {
            "median": statistics.median(job_queue) if job_queue else None,
            "max": max(job_queue) if job_queue else None,
        },
        "within_10pct_of_timeout": near_timeout,
    }


def render_markdown(report: dict[str, Any]) -> str:
    sched = report.get("scheduled", {})
    gaps = sched.get("intervals_minutes", {})
    lines = [
        "| metric | value |",
        "| --- | --- |",
        f"| audit window (UTC) | {report['window_start']} → {report['window_end']} |",
        f"| configured interval | every {report['interval_minutes']} min |",
        f"| runs by event | {report['runs_by_event']} |",
        f"| runs by event:conclusion | {report['runs_by_event_and_conclusion']} |",
        f"| expected scheduled windows | {sched.get('expected_windows')} |",
        f"| observed scheduled runs | {sched.get('observed_runs')} |",
        f"| coverage | {sched.get('coverage_pct')} % |",
        f"| missing windows between runs | {sched.get('missing_windows_between_runs')} |",
        f"| missing windows after last run | {sched.get('missing_windows_after_last_run')} |",
        f"| longest consecutive missing (between runs) | {sched.get('longest_consecutive_missing_between_runs')} |",
        f"| interval median / p90 / p95 / max (min) | {gaps.get('median')} / {gaps.get('p90')} / {gaps.get('p95')} / {gaps.get('max')} |",
        f"| longest gap between | {gaps.get('max_between')} |",
        f"| trailing gap since last scheduled run (min) | {sched.get('trailing_gap_minutes')} |",
        f"| trigger offset past tick, lower bound (median / max min) | {sched.get('trigger_offset_minutes_lower_bound')} |",
        f"| run queue delay (s) | {sched.get('run_queue_delay_seconds')} |",
    ]
    jobs = report.get("jobs")
    if jobs:
        lines += [
            f"| job conclusions | {jobs['conclusions']} |",
            f"| job duration (s) median / p95 / max | {jobs['duration_seconds']} |",
            f"| worker step (s) | {jobs['worker_step_seconds']} |",
            f"| job queue (s) | {jobs['job_queue_seconds']} |",
            f"| timed out / cancelled | {jobs['timed_out']} / {jobs['cancelled']} |",
        ]
    lines += ["", "Limitations:"] + [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines)


def _gh_get(args: list[str], runner: Any = subprocess.run) -> str:
    """Run a READ-ONLY ``gh api`` call. Any mutating flag is refused."""
    if args[:1] != ["api"] or any(flag in args for flag in _FORBIDDEN_GH_ARGS):
        raise ValueError("only read-only `gh api` GET requests are allowed")
    result = runner(["gh", *args], capture_output=True, text=True, check=True)
    return str(result.stdout)


def fetch_runs(repo: str, workflow: str, runner: Any = subprocess.run) -> list[dict[str, Any]]:
    query = (
        ".workflow_runs[] | {id, run_number, event, status, conclusion, created_at, "
        "run_started_at, updated_at, run_attempt, head_sha}"
    )
    out = _gh_get(
        [
            "api",
            "--paginate",
            f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=100",
            "--jq",
            query,
        ],
        runner,
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def fetch_jobs(repo: str, run_ids: list[int], runner: Any = subprocess.run) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for run_id in run_ids:
        out = _gh_get(
            [
                "api",
                f"repos/{repo}/actions/runs/{run_id}/jobs",
                "--jq",
                (
                    ".jobs[] | {name, status, conclusion, created_at, started_at, "
                    "completed_at, steps: [.steps[] | {name, conclusion, started_at, "
                    "completed_at}]}"
                ),
            ],
            runner,
        )
        for line in out.splitlines():
            if line.strip():
                job = json.loads(line)
                job["run_id"] = run_id
                jobs.append(job)
    return jobs


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", help="owner/name (live mode)")
    parser.add_argument("--workflow", default="production-worker.yml")
    parser.add_argument("--runs-file", type=Path, help="offline JSON-lines run records")
    parser.add_argument("--jobs-file", type=Path, help="offline JSON-lines job records")
    parser.add_argument("--with-jobs", action="store_true", help="live: also fetch job timings")
    parser.add_argument("--interval-minutes", type=int, default=5)
    parser.add_argument("--timeout-minutes", type=float, default=None)
    parser.add_argument("--since", help="ISO timestamp: audit window start")
    parser.add_argument("--until", help="ISO timestamp: audit window end (default: now)")
    parser.add_argument("--markdown", action="store_true", help="Markdown table, not JSON")
    args = parser.parse_args(argv)

    if args.interval_minutes < 1:
        parser.error("--interval-minutes must be >= 1")
    if args.runs_file:
        runs = _read_jsonl(args.runs_file)
        jobs = _read_jsonl(args.jobs_file) if args.jobs_file else None
    elif args.repo:
        runs = fetch_runs(args.repo, args.workflow)
        jobs = fetch_jobs(args.repo, [int(r["id"]) for r in runs]) if args.with_jobs else None
    else:
        parser.error("provide --repo (live, read-only) or --runs-file (offline)")

    report = analyze(
        runs,
        jobs,
        interval_minutes=args.interval_minutes,
        window_start=parse_ts(args.since),
        window_end=parse_ts(args.until),
        timeout_minutes=args.timeout_minutes,
    )
    print(render_markdown(report) if args.markdown else json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
