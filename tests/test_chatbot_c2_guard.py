"""Pure unit tests for C2 GitHub Actions-only PostgreSQL/no-provider guard."""
from __future__ import annotations

import pytest

from scripts.chatbot_c2_disposable_guard import (
    DISPOSABLE_URL,
    require_c2_disposable_environment,
)


def _safe() -> dict[str, str]:
    return {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "CXOPS_CHATBOT_C2_OFFLINE": "true",
        "ENVIRONMENT": "development",
        "DEBUG": "false",
        "DATABASE_URL": DISPOSABLE_URL,
        "OPENAI_API_KEY": "",
        "HANDOFF_NOTIFICATIONS_ENABLED": "false",
        "PUBLIC_LEAD_CAPTURE_ENABLED": "false",
        "PUBLIC_CHAT_ASYNC_ENABLED": "false",
    }


def test_exact_disposable_configuration_is_accepted() -> None:
    assert require_c2_disposable_environment(_safe()) == DISPOSABLE_URL


@pytest.mark.parametrize(
    "override",
    [
        {"CI": "false"},
        {"GITHUB_ACTIONS": "false"},
        {"CXOPS_CHATBOT_C2_OFFLINE": ""},
        {"ENVIRONMENT": "production"},
        {"DEBUG": "true"},
        {"HANDOFF_NOTIFICATIONS_ENABLED": "true"},
        {"PUBLIC_LEAD_CAPTURE_ENABLED": "true"},
        {"PUBLIC_CHAT_ASYNC_ENABLED": "true"},
        {"OPENAI_API_KEY": "live-provider-key"},
        {"EVALUATION_OPENAI_API_KEY": "live-provider-key"},
        {"ZENDESK_WEBHOOK_SECRET": "live-secret"},
        {"HANDOFF_NOTIFICATION_EMAIL_URL": "mailtos://credential"},
        {"DATABASE_URL": "postgresql+asyncpg://u:p@db.example.net/cxops"},
        {"DATABASE_URL": "postgresql+asyncpg://c2_probe:c2_probe@localhost:5432/cxops_chatbot_c2_disposable_test"},
        {"DATABASE_URL": DISPOSABLE_URL + "?sslmode=require"},
        {"DATABASE_URL": ""},
    ],
)
def test_any_remote_target_credential_or_unsafe_flag_is_refused(
    override: dict[str, str],
) -> None:
    with pytest.raises(SystemExit, match="C2 guard"):
        require_c2_disposable_environment({**_safe(), **override})
