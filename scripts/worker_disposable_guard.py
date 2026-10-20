"""Fail closed before any disposable real-worker probe imports application code.

This guard is standard-library-only and never opens a socket. A correct URL
still provides no permission to access production or external providers.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from urllib.parse import urlsplit

DISPOSABLE_DB = "cxops_worker_probe_disposable"
DISPOSABLE_USER = "cxops_probe"
DISPOSABLE_PASSWORD = "cxops_probe"
DISPOSABLE_HOST = "127.0.0.1"
DISPOSABLE_PORT = 5432
PROBE_FLAG = "CXOPS_WORKER_DISPOSABLE_PROBE"

FORBIDDEN_VARIABLES = frozenset({
    "ENCRYPTION_KEYS",
    "DATABASE_DIRECT_URL",
    "AUTH_JWT_SECRET",
    "AUTH_JWKS_URL",
    "AUTH_JWT_ISSUER",
    "AUTH_JWT_AUDIENCE",
    "TICKET_EVENT_WEBHOOK_SECRET",
    "HANDOFF_NOTIFICATION_EMAIL_URL",
    "HANDOFF_NOTIFICATION_TELEGRAM_URL",
    "REDIS_URL",
    "CXOPS_STAGING_TICKET_ACTIONS_ENABLED",
})
FORBIDDEN_PREFIXES = (
    "OPENAI_",
    "ZENDESK_",
    "NEON_",
    "NHOST_",
    "AWS_",
    "GCP_",
    "GOOGLE_",
    "SUPABASE_",
    "STRIPE_",
    "SMTP_",
    "SENDGRID_",
    "MAILGUN_",
    "CLOUDFLARE_",
    "RENDER_API_",
    "HANDOFF_",
    "CXOPS_WORKER_CLOCK_",
)


def require_disposable_environment(env: Mapping[str, str]) -> str:
    """Return the exact local DB URL or raise without including secret values."""
    if env.get(PROBE_FLAG) != "true":
        raise ValueError("Disposable probe explicitly disabled")
    if env.get("ENVIRONMENT") != "development":
        raise ValueError("Disposable probe refuses non-development environment")
    if env.get("DEBUG", "false").lower() not in {"false", "0"}:
        raise ValueError("Disposable probe refuses debug logging")
    if env.get("AUTH_MODE", "hs256").lower() != "hs256":
        raise ValueError("Disposable probe refuses external auth configuration")
    if env.get("AUTH_DEV_MODE", "false").lower() not in {"false", "0"}:
        raise ValueError("Disposable probe refuses dev-auth mode")
    if env.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Disposable probe must run in isolated GitHub Actions")

    for name, value in env.items():
        if not value:
            continue
        if name in FORBIDDEN_VARIABLES or name.startswith(FORBIDDEN_PREFIXES):
            raise ValueError("Disposable probe refuses provider credentials")

    url = env.get("DATABASE_URL", "")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Disposable database URL is malformed") from exc
    if (
        parts.scheme != "postgresql+asyncpg"
        or parts.hostname != DISPOSABLE_HOST
        or port != DISPOSABLE_PORT
        or parts.username != DISPOSABLE_USER
        or parts.password != DISPOSABLE_PASSWORD
        or parts.path != "/" + DISPOSABLE_DB
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Disposable probe refuses a non-exact local database")
    return url


def main() -> int:
    try:
        require_disposable_environment(os.environ)
    except ValueError as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2
    print("Disposable worker probe target verified (local, synthetic only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
