"""Offline, side-effect-free Render Cron canary safety and report tests."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.analyze_render_cron_canary import evaluate, parse_utc
from scripts.render_cron_synthetic_canary import DISABLED_EXIT, environment_safe

ROOT = Path(__file__).resolve().parents[1]


def invoke(*args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "CXOPS_RENDER_CRON_SYNTHETIC_ONLY": "true",
        **extra_env,
    }
    return subprocess.run(
        [sys.executable, "-m", "scripts.render_cron_synthetic_canary", *args],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def sample(index: int, base: datetime, *, seconds: int = 12) -> dict:
    start = base + timedelta(minutes=5 * index, seconds=seconds)
    end = start + timedelta(milliseconds=42)
    return {
        "event": "cxops_render_cron_canary_v1",
        "run_id": f"{index:032x}",
        "started_at": start.isoformat().replace("+00:00", "Z"),
        "finished_at": end.isoformat().replace("+00:00", "Z"),
        "elapsed_ms": 42.0,
        "peak_rss_mib": 102.0,
        "synthetic": True,
        "external_calls": 0,
        "database_writes": 0,
    }


class CanaryGuardTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        env = {"PATH": os.environ.get("PATH", "")}
        p = subprocess.run(
            [sys.executable, "-m", "scripts.render_cron_synthetic_canary"],
            env=env,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(p.returncode, DISABLED_EXIT)
        self.assertFalse(p.stdout)

    def test_env_requires_exact_opt_in(self) -> None:
        self.assertFalse(environment_safe({"CXOPS_RENDER_CRON_SYNTHETIC_ONLY": "True"}))
        self.assertTrue(environment_safe({"CXOPS_RENDER_CRON_SYNTHETIC_ONLY": "true"}))

    def test_refuses_production_and_staging(self) -> None:
        for value in ("production", "staging", "PRODUCTION"):
            with self.subTest(value=value):
                p = invoke(ENVIRONMENT=value)
                self.assertEqual(p.returncode, DISABLED_EXIT)
                self.assertFalse(p.stdout)

    def test_refuses_app_and_provider_credentials(self) -> None:
        for name in (
            "DATABASE_URL", "OPENAI_API_KEY", "ENCRYPTION_KEYS",
            "ZENDESK_OAUTH_TOKEN", "NEON_AUTH_URL", "AUTH_JWKS_URL",
            "CLOUDFLARE_API_TOKEN", "SMTP_PASSWORD",
            "HANDOFF_NOTIFICATION_EMAIL_URL",
            "CXOPS_STAGING_TICKET_ACTIONS_ENABLED", "STRIPE_SECRET_KEY",
            "AWS_SECRET_ACCESS_KEY", "REDIS_URL",
        ):
            with self.subTest(name=name):
                p = invoke(**{name: "NON_SECRET_TEST_SENTINEL"})
                self.assertEqual(p.returncode, DISABLED_EXIT)
                self.assertFalse(p.stdout)
                self.assertNotIn("NON_SECRET_TEST_SENTINEL", p.stderr)

    def test_only_standard_library_imports(self) -> None:
        src = ROOT / "scripts" / "render_cron_synthetic_canary.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertTrue(names.issubset({
            "__future__", "argparse", "json", "os", "resource", "sys",
            "time", "uuid", "collections", "datetime",
        }))
        self.assertFalse(names & {"app", "scripts", "socket", "httpx", "requests"})

    def test_success_records_only_synthetic_metadata(self) -> None:
        p = invoke()
        self.assertEqual(p.returncode, 0, p.stderr)
        event = json.loads(p.stdout)
        self.assertEqual(event["event"], "cxops_render_cron_canary_v1")
        self.assertTrue(event["synthetic"])
        self.assertEqual(event["external_calls"], 0)
        self.assertEqual(event["database_writes"], 0)
        self.assertGreater(event["peak_rss_mib"], 0)
        self.assertEqual(len(event["run_id"]), 32)
        self.assertLess(event["elapsed_ms"], 1000)
        self.assertEqual(len(p.stdout.splitlines()), 1)

    def test_rejects_unbounded_delay(self) -> None:
        for delay in ("1001", "-1", "invalid"):
            with self.subTest(delay=delay):
                p = invoke("--synthetic-delay-ms", delay)
                self.assertNotEqual(p.returncode, 0)
                self.assertFalse(p.stdout)

    def test_synthetic_delay_is_bounded(self) -> None:
        p = invoke("--synthetic-delay-ms", "5")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertLess(json.loads(p.stdout)["elapsed_ms"], 500)


class CanaryReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 10, 19, tzinfo=UTC)
        self.end = self.start + timedelta(days=1)
        self.rows = [sample(i, self.start) for i in range(288)]

    def test_full_window_never_certifies_latency(self) -> None:
        result = evaluate(self.rows, start=self.start, end=self.end)
        self.assertEqual(result["status"], "OBSERVATION_ONLY")
        self.assertEqual(result["expected_slots"], 288)
        self.assertFalse(result["schedule_latency_verified"])
        self.assertFalse(result["worker_memory_under_real_provider_load_verified"])

    def test_missing_tick_is_no_go(self) -> None:
        result = evaluate(self.rows[1:], start=self.start, end=self.end)
        self.assertEqual(result["missing_slots"], 1)
        self.assertEqual(result["status"], "NO_GO")

    def test_duplicate_slot_is_no_go(self) -> None:
        dupe = dict(self.rows[0], run_id=uuid.uuid4().hex)
        result = evaluate([*self.rows, dupe], start=self.start, end=self.end)
        self.assertEqual(result["duplicate_start_slots"], 1)
        self.assertEqual(result["status"], "NO_GO")

    def test_observed_phase_above_threshold_is_no_go(self) -> None:
        delayed = [sample(i, self.start, seconds=80) for i in range(288)]
        result = evaluate(delayed, start=self.start, end=self.end)
        self.assertEqual(result["status"], "NO_GO")

    def test_rss_above_threshold_is_no_go(self) -> None:
        high = [{**row, "peak_rss_mib": 385.0} for row in self.rows]
        result = evaluate(high, start=self.start, end=self.end)
        self.assertEqual(result["status"], "NO_GO")

    def test_less_than_24_hours_is_incomplete(self) -> None:
        result = evaluate(
            self.rows[:12], start=self.start, end=self.start + timedelta(hours=1)
        )
        self.assertEqual(result["status"], "INCOMPLETE")

    def test_invalid_marker_or_side_effects_are_rejected(self) -> None:
        updates = (
            {"event": "wrong"},
            {"database_writes": 1},
            {"synthetic": False},
        )
        for update in updates:
            with self.subTest(update=update), self.assertRaises(ValueError):
                evaluate([{**self.rows[0], **update}], start=self.start, end=self.end)

    def test_rejects_repeated_run_identifier(self) -> None:
        with self.assertRaises(ValueError):
            evaluate([self.rows[0], self.rows[0]], start=self.start, end=self.end)

    def test_rejects_unaligned_window(self) -> None:
        with self.assertRaises(ValueError):
            evaluate(
                self.rows,
                start=self.start + timedelta(seconds=1),
                end=self.end,
            )

    def test_utc_input_is_required(self) -> None:
        self.assertEqual(parse_utc("2026-10-19T00:00:00Z"), self.start)
        with self.assertRaises(ValueError):
            parse_utc("2026-10-19T00:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
