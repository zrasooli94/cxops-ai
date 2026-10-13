"""Scheduler comparison metrics and the dead-man state, on synthetic data."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import compare_scheduler_coverage as cmp

T0 = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _run(minute: float, *, status="successful", seconds=45):
    start = T0 + timedelta(minutes=minute)
    return cmp.Execution(
        started_at=start, finished_at=start + timedelta(seconds=seconds), status=status
    )


def _summary(minute: float, *, health="healthy", exit_reason="queue_empty", **extra):
    start = T0 + timedelta(minutes=minute)
    return cmp.Execution(
        started_at=start,
        finished_at=start + timedelta(seconds=30),
        status="completed",
        health=health,
        exit_reason=exit_reason,
        capacity_exhausted=extra.get("capacity_exhausted", False),
        jobs_retried=extra.get("jobs_retried", 0),
        jobs_failed=extra.get("jobs_failed", 0),
    )


def test_perfect_scheduler_has_full_coverage() -> None:
    execs = [_run(5 * n + 0.5) for n in range(12)]
    report = cmp.analyze_label(execs, start=T0, end=T0 + HOUR, interval=timedelta(minutes=5))
    assert report["expected_windows"] == 12
    assert report["windows_covered"] == 12
    assert report["coverage_pct"] == 100.0
    assert report["missed_windows"] == 0
    assert report["late_starts"] == 0


def test_missed_and_late_windows_and_gaps() -> None:
    # On time at 0, 5; LATE (+3 min) at 13; nothing until 50.
    execs = [_run(0.2), _run(5.1), _run(13.0), _run(50.5)]
    report = cmp.analyze_label(execs, start=T0, end=T0 + HOUR, interval=timedelta(minutes=5))
    assert report["windows_covered"] == 4
    assert report["missed_windows"] == 8
    assert report["late_starts"] == 1
    assert report["longest_gap_minutes"] == 37.5


def test_worker_outcomes_are_aggregated() -> None:
    execs = [
        _summary(0),
        _summary(5, health="degraded", jobs_retried=2),
        _summary(10, health="unsuccessful", jobs_failed=1),
        _summary(15, exit_reason="max_seconds", capacity_exhausted=True),
        _summary(20, exit_reason="max_jobs", capacity_exhausted=True),
    ]
    report = cmp.analyze_label(execs, start=T0, end=T0 + HOUR, interval=timedelta(minutes=5))
    assert report["degraded_runs"] == 1
    assert report["unsuccessful_runs"] == 1
    assert report["jobs_retried"] == 2 and report["jobs_failed"] == 1
    assert report["deadline_exhausted"] == 1
    assert report["queue_backlog_runs"] == 2


@pytest.mark.parametrize(
    ("execs", "now_minute", "state"),
    [
        ([], 60, "absent"),
        ([_summary(30)], 60, "absent"),  # last run 30 min ago: absence, not health
        ([_summary(50)], 60, "ok"),
        ([_summary(50, health="degraded")], 60, "degraded"),
        ([_summary(40), _summary(55, health="unsuccessful")], 60, "unsuccessful"),
        ([_run(55)], 60, "unknown_health"),  # ran, but no worker summary
        ([_run(55, status="unsuccessful")], 60, "unsuccessful"),
    ],
)
def test_deadman_distinguishes_absence_from_degraded_execution(execs, now_minute, state) -> None:
    assert cmp.deadman_state(execs, now=T0 + timedelta(minutes=now_minute)) == state


def test_combined_coverage_is_the_union() -> None:
    github = [_run(0.5), _run(30.5)]
    render = [_run(5 * n + 1) for n in range(12) if n != 6]
    report = cmp.compare(
        {"github": github, "render": render}, start=T0, end=T0 + HOUR, interval_minutes=5
    )
    assert report["labels"]["github"]["windows_covered"] == 2
    assert report["labels"]["render"]["windows_covered"] == 11
    assert report["combined"]["windows_covered"] == 12
    text = cmp.render_markdown(report)
    assert "| coverage_pct |" in text and "combined" in text


def test_loaders_normalize_each_source_kind() -> None:
    github = cmp.load_github_runs(
        [
            {"event": "schedule", "created_at": "2026-10-20T00:00:30Z",
             "run_started_at": "2026-10-20T00:00:31Z", "updated_at": "2026-10-20T00:01:10Z",
             "conclusion": "success"},
            {"event": "workflow_dispatch", "created_at": "2026-10-20T00:02:00Z"},
        ]
    )
    assert len(github) == 1 and github[0].duration_seconds == 39.0
    runs = cmp.load_runs(
        [{"started_at": "2026-10-20T00:05:00Z", "finished_at": None, "status": "pending"}]
    )
    assert runs[0].duration_seconds is None
    summaries = cmp.load_summaries(
        [{"event": "worker_once_summary", "timestamp": "2026-10-20T00:10:40Z",
          "elapsed_seconds": 40.0, "health": "degraded", "jobs_retried": 3},
         {"event": "job_completed", "timestamp": "2026-10-20T00:10:41Z"}]
    )
    assert len(summaries) == 1
    assert summaries[0].started_at == datetime(2026, 10, 20, 0, 10, tzinfo=UTC)
    assert summaries[0].jobs_retried == 3


def test_cli_end_to_end(tmp_path: Path, capsys) -> None:
    gh = tmp_path / "gh.jsonl"
    gh.write_text(json.dumps({"event": "schedule", "created_at": "2026-10-20T00:00:20Z",
                              "conclusion": "success"}))
    summaries = tmp_path / "render.jsonl"
    summaries.write_text("\n".join(
        json.dumps({"event": "worker_once_summary",
                    "timestamp": f"2026-10-20T00:{5 * n + 1:02d}:00Z",
                    "elapsed_seconds": 30, "health": "healthy"})
        for n in range(12)
    ))
    code = cmp.main([
        "--source", f"github=github-runs:{gh}",
        "--source", f"render=summaries:{summaries}",
        "--since", "2026-10-20T00:00:00Z", "--until", "2026-10-20T01:00:00Z",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["labels"]["github"]["coverage_pct"] == pytest.approx(8.33, abs=0.01)
    assert report["labels"]["render"]["coverage_pct"] == 100.0
    assert report["labels"]["render"]["deadman_at_end"] == "ok"
    assert report["combined"]["windows_covered"] == 12


@pytest.mark.parametrize(
    "argv",
    [
        ["--source", "x=bogus:/dev/null", "--since", "2026-10-20T00:00:00Z"],
        ["--source", "nolabel", "--since", "2026-10-20T00:00:00Z"],
        ["--source", "x=runs:/dev/null", "--since", "2026-10-20T01:00:00Z",
         "--until", "2026-10-20T00:00:00Z"],
    ],
)
def test_cli_rejects_bad_input(argv) -> None:
    with pytest.raises(SystemExit):
        cmp.main(argv)
