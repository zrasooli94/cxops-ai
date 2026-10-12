#!/usr/bin/env python3
"""Compare execution coverage of two (or more) schedulers driving the worker.

Built for the Phase 1P.9 reliability trial: GitHub Actions (existing) and a
Render Cron Job (candidate) both run the same one-shot worker command. This
tool turns captured metadata into the evidence for the cutover decision. It is
read-only and works offline on files; it never calls any API.

Inputs - one or more ``--source LABEL=KIND:PATH`` (JSON lines):

* ``github-runs``: workflow runs as captured by
  ``scripts/audit_workflow_schedule.py`` (``event``, ``created_at``,
  ``run_started_at``, ``updated_at``, ``conclusion``). Only ``schedule`` runs
  count as scheduled executions.
* ``runs``: a normalized export, one execution per line:
  ``{"started_at": ISO, "finished_at": ISO|null, "status": "successful" |
  "unsuccessful" | "canceled" | ...}`` - e.g. a Render Cron Job run history
  exported from the dashboard. (Render documents run history in the dashboard;
  a public list-runs API is not documented in Render's reference.)
* ``summaries``: the worker's own ``worker_once_summary`` log events as JSON
  lines (``timestamp``, ``health``, ``exit_reason``, ``capacity_exhausted``,
  ``jobs_retried``, ``jobs_failed``, ``elapsed_seconds``, ...). These come from
  the worker itself, so they describe what each run DID, whichever scheduler
  started it.

Metrics per label: expected vs executed windows, coverage, late starts, missed
windows, longest gap, duration, deadline exhaustion, queue backlog (capacity
exhausted), job retries/failures, degraded/unsuccessful runs, and the
dead-man state at the end of the window. With several labels, the combined
(union) coverage is reported too - the safety net while both run.

Dead-man states (see ``deadman_state``):

* ``absent``      - no execution started within the threshold: the scheduler
                    (or the worker start) is not running at all;
* ``unsuccessful``/``degraded`` - executions are happening, but the latest
                    one reported that health;
* ``unknown_health`` - executions are happening but report no health (e.g. a
                    run record without a worker summary);
* ``ok``          - a run started within the threshold and reported healthy.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEADMAN_THRESHOLD = timedelta(minutes=15)
LATE_TOLERANCE = timedelta(minutes=2)
SUCCESS_STATUSES = {"success", "successful", "completed"}


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def floor_to_grid(moment: datetime, interval: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return epoch + ((moment - epoch) // interval) * interval


@dataclass(frozen=True)
class Execution:
    started_at: datetime
    finished_at: datetime | None = None
    status: str | None = None
    health: str | None = None
    exit_reason: str | None = None
    capacity_exhausted: bool | None = None
    jobs_retried: int = 0
    jobs_failed: int = 0

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 1)


@dataclass
class LabelReport:
    label: str
    executions: list[Execution] = field(default_factory=list)


def load_github_runs(rows: Iterable[dict[str, Any]]) -> list[Execution]:
    out = []
    for row in rows:
        if row.get("event") != "schedule":
            continue
        started = parse_ts(row.get("run_started_at")) or parse_ts(row.get("created_at"))
        if started is None:
            continue
        out.append(
            Execution(
                started_at=started,
                finished_at=parse_ts(row.get("updated_at")),
                status=row.get("conclusion"),
            )
        )
    return out


def load_runs(rows: Iterable[dict[str, Any]]) -> list[Execution]:
    out = []
    for row in rows:
        started = parse_ts(row.get("started_at"))
        if started is None:
            continue
        out.append(
            Execution(
                started_at=started,
                finished_at=parse_ts(row.get("finished_at")),
                status=row.get("status"),
            )
        )
    return out


def load_summaries(rows: Iterable[dict[str, Any]]) -> list[Execution]:
    out = []
    for row in rows:
        if row.get("event", "worker_once_summary") != "worker_once_summary":
            continue
        finished = parse_ts(row.get("timestamp"))
        if finished is None:
            continue
        elapsed = row.get("elapsed_seconds")
        started = (
            finished - timedelta(seconds=float(elapsed))
            if isinstance(elapsed, int | float)
            else finished
        )
        out.append(
            Execution(
                started_at=started,
                finished_at=finished,
                status="completed",
                health=row.get("health"),
                exit_reason=row.get("exit_reason"),
                capacity_exhausted=row.get("capacity_exhausted"),
                jobs_retried=int(row.get("jobs_retried") or 0),
                jobs_failed=int(row.get("jobs_failed") or 0),
            )
        )
    return out


LOADERS = {"github-runs": load_github_runs, "runs": load_runs, "summaries": load_summaries}


def deadman_state(
    executions: list[Execution], *, now: datetime, threshold: timedelta = DEADMAN_THRESHOLD
) -> str:
    """Absence of execution is distinguished from a degraded execution."""
    recent = [e for e in executions if now - threshold <= e.started_at <= now]
    if not recent:
        return "absent"
    latest = max(recent, key=lambda e: e.started_at)
    if latest.health in ("unsuccessful", "degraded"):
        return latest.health
    if latest.health == "healthy":
        return "ok"
    if latest.status is not None and latest.status not in SUCCESS_STATUSES:
        return "unsuccessful"
    return "unknown_health"


def _windows(executions: list[Execution], interval: timedelta) -> set[datetime]:
    return {floor_to_grid(e.started_at, interval) for e in executions}


def analyze_label(
    executions: list[Execution],
    *,
    start: datetime,
    end: datetime,
    interval: timedelta,
    late_tolerance: timedelta = LATE_TOLERANCE,
) -> dict[str, Any]:
    window_execs = sorted(
        (e for e in executions if start <= e.started_at < end), key=lambda e: e.started_at
    )
    first_tick = floor_to_grid(start, interval)
    if first_tick < start:
        first_tick += interval
    # Ticks t with first_tick <= t < end.
    expected = math.ceil((end - first_tick) / interval) if end > first_tick else 0
    covered = _windows(window_execs, interval) & {
        first_tick + n * interval for n in range(expected)
    }
    late = [
        e
        for e in window_execs
        if e.started_at - floor_to_grid(e.started_at, interval) > late_tolerance
    ]
    starts = [e.started_at for e in window_execs]
    gaps = [round((b - a).total_seconds() / 60, 2) for a, b in itertools.pairwise(starts)]
    durations = [d for e in window_execs if (d := e.duration_seconds) is not None]
    with_health = [e for e in window_execs if e.health is not None]
    successes = [e for e in window_execs if e.health == "healthy"] or [
        e for e in window_execs if e.health is None and (e.status in SUCCESS_STATUSES)
    ]
    last_success = max((e.started_at for e in successes), default=None)
    return {
        "expected_windows": expected,
        "executions": len(window_execs),
        "windows_covered": len(covered),
        "coverage_pct": round(100 * len(covered) / expected, 2) if expected else None,
        "missed_windows": expected - len(covered),
        "late_starts": len(late),
        "longest_gap_minutes": max(gaps) if gaps else None,
        "duration_seconds": {
            "median": statistics.median(durations) if durations else None,
            "max": max(durations) if durations else None,
        },
        "deadline_exhausted": sum(1 for e in window_execs if e.exit_reason == "max_seconds"),
        "queue_backlog_runs": sum(1 for e in window_execs if e.capacity_exhausted),
        "jobs_retried": sum(e.jobs_retried for e in window_execs),
        "jobs_failed": sum(e.jobs_failed for e in window_execs),
        "runs_with_health": len(with_health),
        "degraded_runs": sum(1 for e in with_health if e.health == "degraded"),
        "unsuccessful_runs": sum(1 for e in with_health if e.health == "unsuccessful")
        + sum(
            1
            for e in window_execs
            if e.health is None and e.status is not None and e.status not in SUCCESS_STATUSES
        ),
        "minutes_since_last_success": (
            round((end - last_success).total_seconds() / 60, 1) if last_success else None
        ),
        "deadman_at_end": deadman_state(window_execs, now=end),
    }


def compare(
    sources: dict[str, list[Execution]],
    *,
    start: datetime,
    end: datetime,
    interval_minutes: int,
) -> dict[str, Any]:
    interval = timedelta(minutes=interval_minutes)
    report: dict[str, Any] = {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "interval_minutes": interval_minutes,
        "labels": {
            label: analyze_label(execs, start=start, end=end, interval=interval)
            for label, execs in sources.items()
        },
    }
    if len(sources) > 1:
        union = [e for execs in sources.values() for e in execs]
        report["combined"] = analyze_label(union, start=start, end=end, interval=interval)
    return report


def render_markdown(report: dict[str, Any]) -> str:
    labels = list(report["labels"])
    columns = [*labels, *(["combined"] if "combined" in report else [])]
    data = {**report["labels"], **({"combined": report["combined"]} if "combined" in report else {})}
    metrics = [
        "expected_windows",
        "executions",
        "coverage_pct",
        "missed_windows",
        "late_starts",
        "longest_gap_minutes",
        "deadline_exhausted",
        "queue_backlog_runs",
        "jobs_retried",
        "jobs_failed",
        "degraded_runs",
        "unsuccessful_runs",
        "minutes_since_last_success",
        "deadman_at_end",
    ]
    lines = [
        (
            f"window {report['window_start']} → {report['window_end']} "
            f"(every {report['interval_minutes']} min)"
        ),
        "",
        "| metric | " + " | ".join(columns) + " |",
        "| --- | " + " | ".join("---" for _ in columns) + " |",
    ]
    for metric in metrics:
        lines.append(
            f"| {metric} | " + " | ".join(str(data[c].get(metric)) for c in columns) + " |"
        )
    return "\n".join(lines)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare scheduler execution coverage.")
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        metavar="LABEL=KIND:PATH",
        help=(
            f"KIND is one of {sorted(LOADERS)}; repeat to merge several files into a "
            "label. Use ONE kind per label (run records OR worker summaries) so an "
            "execution is not counted twice."
        ),
    )
    parser.add_argument("--since", required=True, help="ISO start of the trial window")
    parser.add_argument("--until", help="ISO end (default: now)")
    parser.add_argument("--interval-minutes", type=int, default=5)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)

    start = parse_ts(args.since)
    end = parse_ts(args.until) if args.until else datetime.now(UTC)
    if start is None or end is None or end <= start:
        parser.error("--since/--until must be ISO timestamps with since < until")
    if args.interval_minutes < 1:
        parser.error("--interval-minutes must be >= 1")

    sources: dict[str, list[Execution]] = {}
    for spec in args.source:
        try:
            label, rest = spec.split("=", 1)
            kind, path = rest.split(":", 1)
        except ValueError:
            parser.error(f"bad --source {spec!r}; expected LABEL=KIND:PATH")
        if kind not in LOADERS:
            parser.error(f"unknown source kind {kind!r}")
        sources.setdefault(label, []).extend(LOADERS[kind](_read_jsonl(Path(path))))

    report = compare(sources, start=start, end=end, interval_minutes=args.interval_minutes)
    print(render_markdown(report) if args.markdown else json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
