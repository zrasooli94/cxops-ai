"""Build the OpenAI-backed clients right after the port binds (Phase 1T).

The three provider clients import the OpenAI SDK, the largest single import
cost of the API process. They are constructed lazily so that cost is off the
path that must finish before the port binds, and this warm-up pays it on a
worker thread immediately afterwards, so the first customer turn does not.

Failure here is never fatal and never hides anything: a missing key or a bad
setting is raised again, with the real error, when the first request builds
the client itself.
"""

from __future__ import annotations

import asyncio
import time

from app.core.logging import get_logger

log = get_logger(__name__)


def _build_clients() -> None:
    from app.services.agent_workflow_service import agent_workflow_service
    from app.services.embedding_service import embedding_service
    from app.services.rag_service import rag_service

    # Attribute access runs each ``cached_property`` once.
    _ = embedding_service.client
    _ = rag_service.llm
    _ = agent_workflow_service.decision_llm


async def warm_provider_clients() -> float | None:
    started = time.perf_counter()
    try:
        await asyncio.to_thread(_build_clients)
    except Exception as exc:  # noqa: BLE001 - the request path reports the real error
        log.warning("provider_warmup_failed", error_type=type(exc).__name__)
        return None
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    log.info("provider_warmup_complete", elapsed_ms=elapsed_ms)
    return elapsed_ms
