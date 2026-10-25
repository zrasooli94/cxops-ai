"""Prompt locally and perform the one approved model-metadata GET."""

from __future__ import annotations

import json
import os

from app.services.model_eval_model_access import check_gpt6_luna_model_access
from app.services.model_eval_operator_secret import (
    OperatorSecretInputBlocked,
    run_with_operator_api_key,
)


def _assert_safe_runtime() -> None:
    if "OPENAI_API_KEY" in os.environ or "EVALUATION_OPENAI_API_KEY" in os.environ:
        raise OperatorSecretInputBlocked(
            "model-access verification refuses inherited credential variables"
        )
    environment = os.environ.get("ENVIRONMENT", "development").strip().lower()
    source_flag = os.environ.get("MODEL_EVAL_OPENAI_SOURCE_ENABLED", "false")
    chat_flag = os.environ.get("PUBLIC_CHAT_MODEL_EVAL_ENABLED", "false")
    disabled_values = {"", "0", "false", "no", "off"}
    if environment != "development":
        raise OperatorSecretInputBlocked("model-access verification is development-only")
    if source_flag.strip().lower() not in disabled_values or chat_flag.strip().lower() not in disabled_values:
        raise OperatorSecretInputBlocked(
            "model-access verification requires both activation flags disabled"
        )


def _print_result(
    *, http_status: int | None, metadata_accessible: bool, error_category: str | None
) -> None:
    print(
        json.dumps(
            {
                "error_category": error_category,
                "http_status": http_status,
                "metadata_accessible": metadata_accessible,
            },
            sort_keys=True,
        )
    )


def main() -> int:
    try:
        _assert_safe_runtime()  # Must fail before any secret prompt.
        result = run_with_operator_api_key(check_gpt6_luna_model_access)
    except OperatorSecretInputBlocked:
        _print_result(
            http_status=None,
            metadata_accessible=False,
            error_category="local_preflight_blocked",
        )
        return 2
    except Exception:  # noqa: BLE001 - never expose local/provider exception details
        _print_result(
            http_status=None,
            metadata_accessible=False,
            error_category="local_error",
        )
        return 2
    _print_result(**result.sanitized())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
