"""Side-effect-free, opt-in Render Cron scheduling canary.

No CXOps imports, database access, provider calls, file writes or shell commands.
This is NEVER a production worker entry point.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

CANARY_FLAG = "CXOPS_RENDER_CRON_SYNTHETIC_ONLY"
EVENT_VERSION = "cxops_render_cron_canary_v1"
DISABLED_EXIT = 78
MAX_TEST_DELAY_MS = 1000

_BLOCKED_EXACT = frozenset({
    "DATABASE_URL", "DATABASE_DIRECT_URL", "OPENAI_API_KEY",
    "EVALUATION_OPENAI_API_KEY", "ENCRYPTION_KEYS", "AUTH_JWKS_URL",
    "AUTH_JWT_SECRET", "AUTH_JWT_ISSUER", "HANDOFF_NOTIFICATION_EMAIL_URL",
    "HANDOFF_NOTIFICATION_TELEGRAM_URL",
    "CXOPS_STAGING_TICKET_ACTIONS_ENABLED",
    "CXOPS_WORKER_CLOCK_RECEIVER_ENABLED",
    "CXOPS_WORKER_CLOCK_EXECUTOR_ENABLED", "CLOUDFLARE_API_TOKEN",
})
_BLOCKED_PREFIX = (
    "NEON_", "ZENDESK_", "NHOST_", "SMTP_", "PRODUCTION_",
    "CXOPS_PRODUCTION_", "RENDER_API_KEY", "SUPABASE_", "AWS_", "GCP_",
    "GOOGLE_", "STRIPE_", "REDIS_", "POSTGRES_", "SENTRY_", "SLACK_",
    "TELEGRAM_", "SENDGRID_", "MAILGUN_",
)
_BLOCKED_SUFFIX = (
    "_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_DSN", "_CREDENTIALS",
)


def environment_safe(env: Mapping[str, str]) -> bool:
    """Allow only an explicitly opted-in, isolated and secret-free canary."""
    if env.get(CANARY_FLAG) != "true":
        return False
    if env.get("ENVIRONMENT", "").strip().lower() in {"production", "staging"}:
        return False
    for name, value in env.items():
        if not value:
            continue
        if (
            name in _BLOCKED_EXACT
            or name.endswith(_BLOCKED_SUFFIX)
            or any(name.startswith(prefix) for prefix in _BLOCKED_PREFIX)
        ):
            return False
    return True


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; Darwin reports bytes.
    mib = value / (1024 * 1024) if sys.platform == "darwin" else value / 1024
    return round(mib, 3)


def execute_canary(*, env: Mapping[str, str], delay_ms: int = 0) -> int:
    if not environment_safe(env):
        print(
            "Synthetic Render Cron canary disabled or unsafe environment",
            file=sys.stderr,
        )
        return DISABLED_EXIT
    if not isinstance(delay_ms, int) or not 0 <= delay_ms <= MAX_TEST_DELAY_MS:
        print("Invalid bounded synthetic delay", file=sys.stderr)
        return 2
    started_at = datetime.now(UTC)
    monotonic = time.monotonic_ns()
    if delay_ms:
        time.sleep(delay_ms / 1000)
    finished_at = datetime.now(UTC)
    telemetry = {
        "event": EVENT_VERSION,
        "run_id": uuid.uuid4().hex,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": finished_at.isoformat().replace("+00:00", "Z"),
        "elapsed_ms": round((time.monotonic_ns() - monotonic) / 1_000_000, 3),
        "peak_rss_mib": _rss_mib(),
        "synthetic": True,
        "external_calls": 0,
        "database_writes": 0,
    }
    print(json.dumps(telemetry, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inert Render Cron scheduling canary"
    )
    parser.add_argument("--synthetic-delay-ms", type=int, default=0)
    args = parser.parse_args(argv)
    return execute_canary(env=os.environ, delay_ms=args.synthetic_delay_ms)


if __name__ == "__main__":
    raise SystemExit(main())
