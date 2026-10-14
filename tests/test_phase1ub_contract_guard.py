"""Phase 1U-B negative tests for the disposable-only contract-test boundary.

Safe to run within the normal backend suite; these tests never connect to a DB.
"""
from __future__ import annotations

import pytest

from scripts.api_contract_guard import (
    UnsafeContractTarget,
    validate_disposable_contract_env,
)

SAFE = {
    "CXOPS_API_CONTRACT_RUN": "1",
    "ENVIRONMENT": "development",
    "DEBUG": "false",
    "AUTH_MODE": "hs256",
    "OPENAI_API_KEY": "",
    "EVALUATION_OPENAI_API_KEY": "",
    "PUBLIC_LEAD_CAPTURE_ENABLED": "false",
    "PUBLIC_CHAT_ASYNC_ENABLED": "false",
    "HANDOFF_NOTIFICATIONS_ENABLED": "false",
    "ONBOARDING_KNOWLEDGE_SETUP_ENABLED": "false",
    "DATABASE_URL": (
        "postgresql+asyncpg://cxops_contract:cxops_contract@"
        "127.0.0.1:5432/cxops_contract_disposable_test"
    ),
}


def test_contract_guard_accepts_explicit_disposable_local_database():
    validate_disposable_contract_env(SAFE)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("CXOPS_API_CONTRACT_RUN", ""),
        ("CXOPS_API_CONTRACT_RUN", "0"),
        ("ENVIRONMENT", "production"),
        ("DEBUG", "true"),
        ("AUTH_MODE", "jwks"),
        ("OPENAI_API_KEY", "private-token-canary"),
        ("EVALUATION_OPENAI_API_KEY", "private-token-canary"),
        ("PUBLIC_LEAD_CAPTURE_ENABLED", "true"),
        ("PUBLIC_CHAT_ASYNC_ENABLED", "true"),
        ("HANDOFF_NOTIFICATIONS_ENABLED", "true"),
        ("ONBOARDING_KNOWLEDGE_SETUP_ENABLED", "true"),
        ("DATABASE_URL", "postgresql+asyncpg://u:p@db.example.com:5432/neondb"),
        ("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:5432/neondb"),
        ("DATABASE_URL", "postgresql+asyncpg://cxops_contract:cxops_contract@127.0.0.1:5432/cxops"),
        ("DATABASE_URL", "postgresql+asyncpg://cxops_contract:cxops_contract@127.0.0.1:5433/cxops_contract_disposable_test"),
        ("DATABASE_URL", "postgresql+asyncpg://cxops_contract:wrong@127.0.0.1:5432/cxops_contract_disposable_test"),
        ("DATABASE_URL", "postgresql://cxops_contract:cxops_contract@127.0.0.1:5432/cxops_contract_disposable_test"),
        ("DATABASE_URL", ""),
    ],
)
def test_guard_rejects_unsafe_targets_and_unsafe_provider_flags(field, bad):
    unsafe = dict(SAFE)
    unsafe[field] = bad
    with pytest.raises(UnsafeContractTarget) as caught:
        validate_disposable_contract_env(unsafe)
    assert "private-token-canary" not in str(caught.value)
    assert "neondb" not in str(caught.value)


def test_contract_guard_does_not_accept_missing_controls():
    with pytest.raises(UnsafeContractTarget):
        validate_disposable_contract_env({})


def test_contract_guard_rejects_remote_host_even_with_disposable_database_name():
    remote = dict(SAFE)
    remote["DATABASE_URL"] = SAFE["DATABASE_URL"].replace("127.0.0.1", "api.neon.tech")
    with pytest.raises(UnsafeContractTarget):
        validate_disposable_contract_env(remote)
