"""Disabled-by-design HTTP seam for the bounded Responses evaluation.

The seam accepts only an injected, explicitly disconnected client.  It neither
constructs an HTTP client nor accepts credentials, headers, endpoint overrides,
or retry configuration.  A future real transport therefore requires a separate
source change and review rather than a configuration toggle.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from app.services.model_eval_controller import (
    ConfirmedNoDispatchError,
    EvaluationBlockedError,
)


class DisconnectedHttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Any: ...


class DisconnectedHttpClient(Protocol):
    provider_disconnected: bool
    max_retries: int

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> DisconnectedHttpResponse: ...


class ProviderDisconnectedResponsesHttpTransport:
    """Validate and exercise the future HTTP boundary with mocked clients only."""

    provider_disconnected = True
    # Intentionally non-routable. A later approved source change must introduce
    # the documented live HTTPS endpoint and an authenticated client.
    ENDPOINT = "mock://provider-disconnected/v1/responses"
    TIMEOUT_SECONDS = 30.0
    MAX_REQUEST_BYTES = 65_536
    _PAYLOAD_KEYS = frozenset({
        "model",
        "input",
        "reasoning",
        "max_output_tokens",
        "service_tier",
        "prompt_cache_options",
        "store",
        "stream",
        "text",
    })

    def __init__(self, client: DisconnectedHttpClient) -> None:
        if not getattr(client, "provider_disconnected", False):
            raise EvaluationBlockedError("only an injected disconnected HTTP client is allowed")
        if getattr(client, "max_retries", None) != 0:
            raise EvaluationBlockedError("the disconnected HTTP client must disable retries")
        self._client = client

    async def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_payload(payload)
        serialized = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        if len(serialized) > self.MAX_REQUEST_BYTES:
            raise ConfirmedNoDispatchError("request exceeds the fixed byte boundary")

        # This client is injected and disconnected. Authorization is deliberately
        # absent, so this class cannot become a live provider path via environment.
        try:
            response = await self._client.post(
                self.ENDPOINT,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.TIMEOUT_SECONDS,
            )
        except ConfirmedNoDispatchError:
            raise
        except Exception:  # noqa: BLE001 - mock client diagnostics may contain secrets
            raise EvaluationBlockedError(
                "disconnected HTTP transport outcome is ambiguous"
            ) from None
        status = response.status_code
        if not isinstance(status, int) or isinstance(status, bool):
            raise EvaluationBlockedError("provider returned an invalid HTTP status")
        if status < 200 or status >= 300:
            # Never include response bodies, headers, request ids, or exception text:
            # those can echo credentials, prompts, or provider diagnostics.
            raise EvaluationBlockedError(f"provider returned HTTP status {status}")
        content_type = str(response.headers.get("content-type", "")).lower()
        if "text/event-stream" in content_type:
            raise EvaluationBlockedError("streamed Responses are not supported")
        if "application/json" not in content_type:
            raise EvaluationBlockedError("provider response is not JSON")
        try:
            body = response.json()
        except (TypeError, ValueError):
            raise EvaluationBlockedError("provider returned malformed JSON") from None
        if not isinstance(body, dict):
            raise EvaluationBlockedError("provider response envelope is not an object")
        return body

    @classmethod
    def _validate_payload(cls, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or set(payload) != cls._PAYLOAD_KEYS:
            raise ConfirmedNoDispatchError("request does not match the fixed Responses schema")
        if (
            payload.get("model") != "gpt-6-luna"
            or payload.get("service_tier") != "default"
            or payload.get("max_output_tokens") != 500
            or payload.get("reasoning") != {"effort": "none"}
            or payload.get("prompt_cache_options") != {"ttl": "30m"}
            or payload.get("store") is not False
            or payload.get("stream") is not False
            or not isinstance(payload.get("input"), str)
            or not payload["input"]
        ):
            raise ConfirmedNoDispatchError("request violates the fixed evaluation policy")
        # Pin the entire request, including the output schema/name. Only the
        # serialized input is variable.
        from app.services.model_eval_openai_adapter import (
            ProviderDisconnectedResponsesAdapter,
        )

        if payload != ProviderDisconnectedResponsesAdapter.request_payload(payload["input"]):
            raise ConfirmedNoDispatchError("structured JSON output contract changed")
