"""Hard fail-closed boundary for the Phase 1U-B disposable Schemathesis pilot.

No app import, database connection, model invocation or HTTP request is
allowed before validate_disposable_contract_env succeeds.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

DISPOSABLE_DATABASE_NAME = "cxops_contract_disposable_test"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DISABLED_VALUES = frozenset({"", "0", "false", "off", "no"})


class UnsafeContractTarget(RuntimeError):
    """Refuse API fuzzing when isolation cannot be established."""


def validate_disposable_contract_env(environ: Mapping[str, str] | None = None) -> None:
    """Accept only an explicitly opted-in, locally hosted disposable database.

    `case.call()` will use an in-process ASGI client, not a remote base URL.
    Never print the DSN or secrets in an error message.
    """
    env = os.environ if environ is None else environ
    if env.get("CXOPS_API_CONTRACT_RUN") != "1":
        raise UnsafeContractTarget("contract pilot requires explicit CXOPS_API_CONTRACT_RUN=1")
    if env.get("ENVIRONMENT") != "development" or env.get("DEBUG", "").lower() != "false":
        raise UnsafeContractTarget("contract pilot requires non-debug development configuration")
    if env.get("AUTH_MODE") != "hs256":
        raise UnsafeContractTarget("contract pilot requires isolated test authentication")
    if env.get("OPENAI_API_KEY") or env.get("EVALUATION_OPENAI_API_KEY"):
        raise UnsafeContractTarget("contract pilot must have no AI provider keys")
    for key in (
        "PUBLIC_LEAD_CAPTURE_ENABLED",
        "PUBLIC_CHAT_ASYNC_ENABLED",
        "HANDOFF_NOTIFICATIONS_ENABLED",
        "ONBOARDING_KNOWLEDGE_SETUP_ENABLED",
    ):
        if env.get(key, "false").strip().lower() not in DISABLED_VALUES:
            raise UnsafeContractTarget(f"contract pilot requires {key} to be disabled")
    raw = env.get("DATABASE_URL", "")
    try:
        url = make_url(raw)
    except (ArgumentError, TypeError, ValueError):
        raise UnsafeContractTarget("missing or malformed disposable database URL") from None
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in LOCAL_HOSTS
        or url.port != 5432
        or url.database != DISPOSABLE_DATABASE_NAME
        or url.username != "cxops_contract"
        or url.password != "cxops_contract"
        or bool(url.query)
    ):
        raise UnsafeContractTarget("contract pilot refuses non-local/non-disposable database target")


def main() -> int:
    try:
        validate_disposable_contract_env()
    except UnsafeContractTarget as exc:
        print(f"CONTRACT_SAFETY_REFUSED: {exc}", file=sys.stderr)
        return 2
    print("CONTRACT_TARGET_LOCAL_DISPOSABLE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
