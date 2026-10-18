"""Offline analysis of *JSON-only* synthetic canary event logs.

No external requests, file writes, credentials or production data.
Never treats observed five-minute clock phase as certified scheduled-start lag.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SLOT_SECONDS = 300
MIN_OBSERVATION_SECONDS = 24 * 3600
RSS_LIMIT_MIB = 384.0
PHASE_LIMIT_SECONDS = 60.0
EVENT_VERSION = "cxops_render_cron_canary_v1"


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError("Expected an ISO 8601 UTC string")
    if not value.endswith("Z"):
        raise ValueError("Expected UTC time ending Z")
    dt = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if dt.utcoffset() is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError("Expected a UTC offset")
    return dt.astimezone(UTC)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


def evaluate(
    rows: list[dict[str, Any]], *, start: datetime, end: datetime
) -> dict[str, Any]:
    """Evaluate actual-start observations, not certified Cron dispatch times."""
    begin = start.timestamp()
    finish = end.timestamp()
    if (
        finish <= begin
        or begin % SLOT_SECONDS != 0
        or finish % SLOT_SECONDS != 0
    ):
        raise ValueError("Window endpoints must align to UTC five-minute slots")
    expected_slots = int((finish - begin) // SLOT_SECONDS)
    observed: Counter[int] = Counter()
    phases: list[float] = []
    rss: list[float] = []
    seen_run_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("Malformed canary event")
        if row.get("event") != EVENT_VERSION:
            raise ValueError("Missing or wrong canary event marker")
        if (
            row.get("synthetic") is not True
            or row.get("external_calls") != 0
            or row.get("database_writes") != 0
        ):
            raise ValueError("Unsafe/non-synthetic telemetry")
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or len(run_id) != 32:
            raise ValueError("Invalid canary run identifier")
        if run_id in seen_run_ids:
            raise ValueError("Duplicate run identifier")
        seen_run_ids.add(run_id)
        started = parse_utc(row.get("started_at"))
        ended = parse_utc(row.get("finished_at"))
        ts = started.timestamp()
        if not begin <= ts < finish or ended < started:
            raise ValueError("Event outside window or impossible time")
        peak = row.get("peak_rss_mib")
        elapsed = row.get("elapsed_ms")
        if (
            not isinstance(peak, (int, float))
            or not math.isfinite(peak)
            or peak <= 0
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed)
            or elapsed < 0
        ):
            raise ValueError("Invalid runtime or memory telemetry")
        observed[int(ts // SLOT_SECONDS)] += 1
        phases.append(ts % SLOT_SECONDS)
        rss.append(float(peak))
    expected = range(int(begin // SLOT_SECONDS), int(finish // SLOT_SECONDS))
    missing = sum(1 for slot in expected if observed[slot] == 0)
    duplicates = sum(count - 1 for count in observed.values() if count > 1)
    p95_phase = round(_percentile(phases, 0.95), 3)
    p95_rss = round(_percentile(rss, 0.95), 3)
    incomplete = (finish - begin) < MIN_OBSERVATION_SECONDS
    fail = (
        missing > 0
        or duplicates > 0
        or p95_phase > PHASE_LIMIT_SECONDS
        or p95_rss >= RSS_LIMIT_MIB
    )
    return {
        "status": (
            "INCOMPLETE" if incomplete else "NO_GO" if fail else "OBSERVATION_ONLY"
        ),
        "expected_slots": expected_slots,
        "events": len(rows),
        "missing_slots": missing,
        "duplicate_start_slots": duplicates,
        "p95_observed_utc_phase_seconds": p95_phase,
        "p95_peak_rss_mib": p95_rss,
        "schedule_latency_verified": False,
        "worker_memory_under_real_provider_load_verified": False,
        "provider_run_history_reconciled": False,
        "notes": (
            "UTC modulo-five-minute phase is NOT a trusted scheduled-start "
            "timestamp. Cross-check Render run history, failures, cancellations, "
            "billing and scheduling evidence before release approval."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Render Cron canary log assessor"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args(argv)
    try:
        rows = [
            json.loads(line)
            for line in args.input.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        result = evaluate(
            rows, start=parse_utc(args.start), end=parse_utc(args.end)
        )
    except (ValueError, TypeError, OSError) as exc:
        parser.error(
            "Invalid/untrusted canary observation dataset: "
            + type(exc).__name__
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "OBSERVATION_ONLY" else 1


if __name__ == "__main__":
    sys.exit(main())
