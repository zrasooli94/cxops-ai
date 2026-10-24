from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.services.model_eval_controller import (
    EvaluationBlockedError,
    EvaluationPolicy,
    InputTokenCountPolicy,
)
from app.services.model_eval_development_lifecycle import (
    installed_development_evaluation_provider,
)
from app.services.public_chat_service import PublicChatService


class _Client:
    max_retries = 0

    async def post(self, *_args, **_kwargs):
        raise AssertionError("lifecycle construction must not dispatch")


def _count_policy():
    return InputTokenCountPolicy(
        endpoint="https://api.openai.com/v1/responses/input_tokens",
        model="gpt-6-luna",
        region="standard",
        billing_status="no_charge",
        reservation_per_attempt_usd=0,
        evidence_id="synthetic-verified-evidence",
        evidence_expires_at=datetime.now(UTC) + timedelta(minutes=30),
        independently_verified=True,
        explicitly_approved=True,
    )


@pytest.mark.asyncio
async def test_lifecycle_is_explicit_development_only_and_rolls_back(monkeypatch):
    PublicChatService.model_evaluation_provider = None
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "model_eval_openai_source_enabled", True)
    async with installed_development_evaluation_provider(
        run_id="synthetic-run",
        http_client=_Client(),
        bearer_token="synthetic-token",
        count_policy=_count_policy(),
        evaluation_policy=EvaluationPolicy(additional_all_in_reserve_usd=0.01),
        explicit_operator_activation=True,
    ) as provider:
        assert PublicChatService.model_evaluation_provider is provider
    assert PublicChatService.model_evaluation_provider is None

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(EvaluationBlockedError, match="disabled"):
        async with installed_development_evaluation_provider(
            run_id="synthetic-run",
            http_client=_Client(),
            bearer_token="synthetic-token",
            count_policy=_count_policy(),
            evaluation_policy=EvaluationPolicy(),
            explicit_operator_activation=True,
        ):
            pass
