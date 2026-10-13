"""The schedule audit must compute gaps exactly, label what it cannot know,
and only ever issue read-only GitHub API requests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import audit_workflow_schedule as audit

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _run(minutes: float, *, event: str = "schedule", conclusion: str = "success", **extra):
    created = T0 + timedelta(minutes=minutes)
    stamp = created.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": int(minutes * 100),
        "event": event,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": stamp,
        "run_started_at": extra.get("run_started_at", stamp),
        **{k: v for k, v in extra.items() if k != "run_started_at"},
    }


def test_perfect_five_minute_schedule_has_full_coverage() -> None:
    runs = [_run(5 * n) for n in range(12)]
    report = audit.analyze(
        runs, None, interval_minutes=5, window_start=T0, window_end=T0 + timedelta(minutes=55)
    )
    sched = report["scheduled"]
    assert sched["expected_windows"] == 12
    assert sched["observed_runs"] == 12
    assert sched["coverage_pct"] == 100.0
    assert sched["missing_windows_between_runs"] == 0
    assert sched["intervals_minutes"]["median"] == 5.0


def test_gaps_missing_windows_and_longest_streak_are_exact() -> None:
    # Runs at 0, 5, 60, 65 and 300 minutes: 10 then 46 windows missing.
    runs = [_run(m) for m in (0, 5, 60, 65, 300)]
    report = audit.analyze(
        runs, None, interval_minutes=5, window_start=T0, window_end=T0 + timedelta(minutes=300)
    )
    sched = report["scheduled"]
    assert sched["missing_windows_between_runs"] == 10 + 46
    assert sched["longest_consecutive_missing_between_runs"] == 46
    assert sched["intervals_minutes"]["max"] == 235.0
    assert sched["intervals_minutes"]["max_between"][0].startswith("2026-10-07T13:05")
    assert sched["expected_windows"] == 61


def test_trailing_gap_after_the_last_run_is_reported() -> None:
    runs = [_run(0), _run(5)]
    report = audit.analyze(
        runs, None, interval_minutes=5, window_start=T0, window_end=T0 + timedelta(minutes=125)
    )
    sched = report["scheduled"]
    assert sched["trailing_gap_minutes"] == 120.0
    assert sched["missing_windows_after_last_run"] == 24
    assert sched["last_scheduled_run"].startswith("2026-10-07T12:05")


def test_manual_runs_are_counted_but_never_fill_schedule_windows() -> None:
    runs = [_run(0), _run(30, event="workflow_dispatch"), _run(60)]
    report = audit.analyze(
        runs, None, interval_minutes=5, window_start=T0, window_end=T0 + timedelta(minutes=60)
    )
    assert report["runs_by_event"] == {"schedule": 2, "workflow_dispatch": 1}
    assert report["scheduled"]["observed_runs"] == 2
    assert report["scheduled"]["missing_windows_between_runs"] == 11


def test_failed_and_cancelled_runs_are_distinguished_from_missing_ones() -> None:
    runs = [_run(0), _run(5, conclusion="failure"), _run(10, conclusion="cancelled")]
    report = audit.analyze(
        runs, None, interval_minutes=5, window_start=T0, window_end=T0 + timedelta(minutes=10)
    )
    assert report["runs_by_event_and_conclusion"] == {
        "schedule:cancelled": 1,
        "schedule:failure": 1,
        "schedule:success": 1,
    }
    assert report["scheduled"]["missing_windows_between_runs"] == 0


def test_trigger_offset_is_a_labelled_lower_bound_and_queue_delay_measured() -> None:
    started = (T0 + timedelta(minutes=8, seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = [_run(3), _run(8, run_started_at=started)]
    report = audit.analyze(
        runs, None, interval_minutes=5, window_start=T0, window_end=T0 + timedelta(minutes=10)
    )
    sched = report["scheduled"]
    assert sched["trigger_offset_minutes_lower_bound"] == {"median": 3.0, "max": 3.0}
    assert sched["run_queue_delay_seconds"]["max"] == 30.0
    assert any("lower bound" in note for note in report["limitations"])
    assert any("never fired" in note for note in report["limitations"])


def test_job_durations_timeouts_and_worker_step() -> None:
    def job(start_min: float, seconds: float, conclusion: str = "success"):
        start = T0 + timedelta(minutes=start_min)
        end = start + timedelta(seconds=seconds)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        return {
            "conclusion": conclusion,
            "created_at": (start - timedelta(seconds=2)).strftime(fmt),
            "started_at": start.strftime(fmt),
            "completed_at": end.strftime(fmt),
            "steps": [
                {
                    "name": audit.WORKER_STEP_NAME,
                    "started_at": start.strftime(fmt),
                    "completed_at": (start + timedelta(seconds=seconds / 2)).strftime(fmt),
                }
            ],
        }

    jobs = [job(0, 40), job(5, 60), job(10, 350, "timed_out")]
    report = audit.analyze(
        [_run(0)], jobs, interval_minutes=5, window_start=T0, window_end=T0, timeout_minutes=6
    )
    stats = report["jobs"]
    assert stats["count"] == 3
    assert stats["timed_out"] == 1
    assert stats["duration_seconds"]["max"] == 350.0
    assert stats["worker_step_seconds"]["median"] == 30.0
    assert stats["job_queue_seconds"]["median"] == 2.0
    assert stats["within_10pct_of_timeout"] == 1


def test_empty_history_is_reported_not_invented() -> None:
    report = audit.analyze([], None, interval_minutes=5, window_end=T0)
    assert report["runs_total"] == 0
    assert report["scheduled"] == {"count": 0}


@pytest.mark.parametrize(
    ("values", "pct", "expected"),
    [([1, 2, 3, 4], 50, 2), ([1, 2, 3, 4], 95, 4), ([7], 90, 7), ([], 50, None)],
)
def test_percentile_nearest_rank(values, pct, expected) -> None:
    assert audit.percentile(values, pct) == expected


def test_markdown_rendering_contains_key_metrics() -> None:
    runs = [_run(m) for m in (0, 60)]
    report = audit.analyze(
        runs, None, interval_minutes=5, window_start=T0, window_end=T0 + timedelta(minutes=60)
    )
    text = audit.render_markdown(report)
    assert "| observed scheduled runs | 2 |" in text
    assert "Limitations:" in text


# ---------------------------------------------------------------------------
# Read-only GitHub access
# ---------------------------------------------------------------------------


class FakeGh:
    def __init__(self, outputs: dict[str, str]) -> None:
        self.calls: list[list[str]] = []
        self.outputs = outputs

    def __call__(self, cmd, capture_output, text, check):
        self.calls.append(cmd)
        for key, value in self.outputs.items():
            if key in " ".join(cmd):
                return SimpleNamespace(stdout=value)
        return SimpleNamespace(stdout="")


def test_fetch_uses_only_get_requests() -> None:
    run_line = json.dumps(_run(0))
    job_line = json.dumps({"name": "One-shot worker", "conclusion": "success", "steps": []})
    gh = FakeGh({"workflows/production-worker.yml/runs": run_line, "/jobs": job_line})

    runs = audit.fetch_runs("o/r", "production-worker.yml", runner=gh)
    jobs = audit.fetch_jobs("o/r", [runs[0]["id"]], runner=gh)

    assert len(runs) == 1 and jobs[0]["run_id"] == runs[0]["id"]
    for cmd in gh.calls:
        assert cmd[:2] == ["gh", "api"]
        assert not set(cmd) & {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}


@pytest.mark.parametrize(
    "args",
    [
        ["api", "-X", "POST", "repos/o/r/actions/workflows/w.yml/dispatches"],
        ["api", "--method", "DELETE", "repos/o/r/actions/runs/1"],
        ["api", "repos/o/r/actions/runs/1/rerun", "-f", "x=1"],
        ["workflow", "run", "w.yml"],
    ],
)
def test_mutating_gh_calls_are_refused(args) -> None:
    def _never(*_a, **_k):
        raise AssertionError("must not execute")

    with pytest.raises(ValueError):
        audit._gh_get(args, runner=_never)


def test_offline_cli_end_to_end(tmp_path: Path, capsys) -> None:
    runs_file = tmp_path / "runs.jsonl"
    runs_file.write_text("\n".join(json.dumps(_run(m)) for m in (0, 5, 60)))

    code = audit.main(
        [
            "--runs-file",
            str(runs_file),
            "--since",
            "2026-10-07T12:00:00Z",
            "--until",
            "2026-10-07T13:00:00Z",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["scheduled"]["observed_runs"] == 3
    assert payload["scheduled"]["missing_windows_between_runs"] == 10


def test_cli_requires_a_source() -> None:
    with pytest.raises(SystemExit):
        audit.main([])
