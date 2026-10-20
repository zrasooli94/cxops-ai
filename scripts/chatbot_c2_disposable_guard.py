"""Hard stop before importing CXOps or migrating any non-disposable database.

This is NOT a general migration utility. The C2 GitHub Actions test lane may
only touch its exact, disposable loopback PostgreSQL service. No real provider
credentials, customer tenancy, live OpenAI, Render/Neon or remote database.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

DISPOSABLE_DB = "cxops_chatbot_c2_disposable_test"
DISPOSABLE_URL = (
    "postgresql+asyncpg://c2_probe:c2_probe@127.0.0.1:5432/"
    + DISPOSABLE_DB
)


def require_c2_disposable_environment(
    env: Mapping[str, str],
) -> str:
    if any(
        env.get(key) != expected
        for key, expected in (
            ("CI", "true"),
            ("GITHUB_ACTIONS", "true"),
            ("CXOPS_CHATBOT_C2_OFFLINE", "true"),
            ("ENVIRONMENT", "development"),
            ("DEBUG", "false"),
            ("HANDOFF_NOTIFICATIONS_ENABLED", "false"),
            ("PUBLIC_LEAD_CAPTURE_ENABLED", "false"),
            ("PUBLIC_CHAT_ASYNC_ENABLED", "false"),
        )
    ):
        raise SystemExit("C2 guard: not an exact offline GitHub Actions environment")

    for secret in (
        "OPENAI_API_KEY",
        "EVALUATION_OPENAI_API_KEY",
        "ZENDESK_WEBHOOK_SECRET",
        "ZENDESK_CLIENT_SECRET",
        "ZENDESK_CLIENT_ID",
        "HANDOFF_NOTIFICATION_EMAIL_URL",
        "HANDOFF_NOTIFICATION_TELEGRAM_URL",
    ):
        if env.get(secret, ""):
            raise SystemExit("C2 guard: provider credentials must be empty")

    url = env.get("DATABASE_URL", "")
    if url != DISPOSABLE_URL:
        raise SystemExit("C2 guard: database is not the exact local disposable target")
    parsed = urlsplit(url)
    if not (
        parsed.scheme == "postgresql+asyncpg"
        and parsed.hostname == "127.0.0.1"
        and parsed.port == 5432
        and parsed.path == "/" + DISPOSABLE_DB
        and parsed.username == "c2_probe"
        and parsed.password == "c2_probe"
        and not parsed.query
        and not parsed.fragment
    ):
        raise SystemExit("C2 guard: unsafe PostgreSQL target")

    return url


if __name__ == "__main__":
    require_c2_disposable_environment(os.environ)
    print("C2 offline target verified: ephemeral loopback PostgreSQL; no providers.")
