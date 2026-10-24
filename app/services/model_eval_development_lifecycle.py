"""Explicit development-only installation of the approved evaluation provider."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import settings
from app.services.model_eval_controller import EvaluationPolicy, InputTokenCountPolicy
from app.services.model_eval_openai_adapter import ProviderDisconnectedResponsesAdapter
from app.services.model_eval_openai_http_transport import (
    DevelopmentExactInputTokenCountTransport,
    DevelopmentHttpClient,
    DevelopmentOpenAIResponsesHttpSource,
)
from app.services.public_chat_model_eval import PublicChatModelEvaluationProvider
from app.services.public_chat_service import PublicChatService


@asynccontextmanager
async def installed_development_evaluation_provider(
    *,
    run_id: str,
    http_client: DevelopmentHttpClient,
    bearer_token: str,
    count_policy: InputTokenCountPolicy,
    evaluation_policy: EvaluationPolicy,
    explicit_operator_activation: bool,
) -> AsyncIterator[PublicChatModelEvaluationProvider]:
    """Install for one local process lifetime and always roll back on exit."""
    if PublicChatService.model_evaluation_provider is not None:
        raise RuntimeError("a model evaluation provider is already installed")
    source = DevelopmentOpenAIResponsesHttpSource(
        http_client,
        environment=settings.environment,
        feature_enabled=settings.model_eval_openai_source_enabled,
        explicitly_approved=explicit_operator_activation,
        bearer_token=bearer_token,
    )
    adapter = ProviderDisconnectedResponsesAdapter(
        transport=source,
        explicitly_approved=explicit_operator_activation,
        development_provider_source_enabled=settings.model_eval_openai_source_enabled,
        environment=settings.environment,
    )
    provider = PublicChatModelEvaluationProvider(
        run_id=run_id,
        count_policy=count_policy,
        count_transport=DevelopmentExactInputTokenCountTransport(source),
        adapter=adapter,
        evaluation_policy=evaluation_policy,
    )
    PublicChatService.model_evaluation_provider = provider
    try:
        yield provider
    finally:
        if PublicChatService.model_evaluation_provider is provider:
            PublicChatService.model_evaluation_provider = None
