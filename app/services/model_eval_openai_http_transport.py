"""HTTP boundaries for the bounded Responses evaluation.

The default seam accepts only an injected disconnected client. A separate real
HTTPS source exists for future development composition, but checked-in settings
disable it and authenticated full-request counting remains fail-closed because
its request cost has not been verified or included in the evaluation ceiling.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Protocol

from app.services.model_eval_controller import (
    ConfirmedNoDispatchError,
    EvaluationBlockedError,
    ExactInputTokenCountResult,
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
    _PAYLOAD_KEYS = frozenset(
        {
            "model",
            "input",
            "reasoning",
            "max_output_tokens",
            "service_tier",
            "prompt_cache_options",
            "store",
            "stream",
            "truncation",
            "text",
        }
    )

    def __init__(self, client: DisconnectedHttpClient) -> None:
        if not getattr(client, "provider_disconnected", False):
            raise EvaluationBlockedError(
                "only an injected disconnected HTTP client is allowed"
            )
        if getattr(client, "max_retries", None) != 0:
            raise EvaluationBlockedError(
                "the disconnected HTTP client must disable retries"
            )
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
            raise ConfirmedNoDispatchError(
                "request does not match the fixed Responses schema"
            )
        if (
            payload.get("model") != "gpt-6-luna"
            or payload.get("service_tier") != "default"
            or payload.get("max_output_tokens") != 500
            or payload.get("reasoning") != {"effort": "none"}
            or payload.get("prompt_cache_options") != {"mode": "explicit", "ttl": "30m"}
            or payload.get("store") is not False
            or payload.get("stream") is not False
            or payload.get("truncation") != "disabled"
            or not isinstance(payload.get("input"), str)
            or not payload["input"]
        ):
            raise ConfirmedNoDispatchError(
                "request violates the fixed evaluation policy"
            )
        # Pin the entire request, including the output schema/name. Only the
        # serialized input is variable.
        from app.services.model_eval_openai_adapter import (
            ProviderDisconnectedResponsesAdapter,
        )

        if payload != ProviderDisconnectedResponsesAdapter.request_payload(
            payload["input"]
        ):
            raise ConfirmedNoDispatchError("structured JSON output contract changed")


class DevelopmentHttpClient(Protocol):
    """Minimal injected client contract; the transport never constructs a client."""

    max_retries: int

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> DisconnectedHttpResponse: ...


class DevelopmentOpenAIResponsesHttpSource:
    """Real HTTPS source path, unreachable under checked-in defaults.

    The class is deliberately not wired into application startup. Construction
    requires a development environment, a separately enabled feature flag,
    explicit evaluation approval, an injected no-retry client, and an injected
    bearer credential. Tests use synthetic values and never open a socket.
    """

    provider_disconnected = False
    RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
    INPUT_TOKENS_ENDPOINT = "https://api.openai.com/v1/responses/input_tokens"
    TIMEOUT_SECONDS = 30.0
    MAX_REQUEST_BYTES = 65_536

    def __init__(
        self,
        client: DevelopmentHttpClient,
        *,
        environment: str,
        feature_enabled: bool,
        explicitly_approved: bool,
        bearer_token: str,
    ) -> None:
        if environment != "development" or not feature_enabled:
            raise EvaluationBlockedError("development provider source is disabled")
        if not explicitly_approved:
            raise EvaluationBlockedError("evaluation approval is required")
        if getattr(client, "max_retries", None) != 0:
            raise EvaluationBlockedError("provider HTTP client must disable retries")
        if not bearer_token or any(char.isspace() for char in bearer_token):
            raise EvaluationBlockedError(
                "an injected restricted credential is required"
            )
        self._client = client
        self._headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> None:
        ProviderDisconnectedResponsesHttpTransport._validate_payload(payload)

    @staticmethod
    def _payload_for_input(serialized_input: str) -> dict[str, Any]:
        from app.services.model_eval_openai_adapter import (
            ProviderDisconnectedResponsesAdapter,
        )

        return ProviderDisconnectedResponsesAdapter.request_payload(serialized_input)

    async def count_input_tokens(self, payload: dict[str, Any]) -> int:
        """Count the exact canonical request after controller-side admission."""
        self._validate_payload(payload)
        response = await self._post(self.INPUT_TOKENS_ENDPOINT, payload)
        try:
            body = response.json()
        except (TypeError, ValueError):
            raise EvaluationBlockedError(
                "provider returned malformed count JSON"
            ) from None
        count = body.get("input_tokens") if isinstance(body, dict) else None
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise EvaluationBlockedError(
                "provider returned an invalid input-token count"
            )
        return count

    async def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_payload(payload)
        response = await self._post(self.RESPONSES_ENDPOINT, payload)
        try:
            body = response.json()
        except (TypeError, ValueError):
            raise EvaluationBlockedError("provider returned malformed JSON") from None
        if not isinstance(body, dict):
            raise EvaluationBlockedError("provider response envelope is not an object")
        return body

    async def _post(
        self, endpoint: str, payload: dict[str, Any]
    ) -> DisconnectedHttpResponse:
        size = len(
            json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        )
        if size > self.MAX_REQUEST_BYTES:
            raise ConfirmedNoDispatchError("request exceeds the fixed byte boundary")
        try:
            response = await self._client.post(
                endpoint,
                json=payload,
                headers=self._headers,
                timeout=self.TIMEOUT_SECONDS,
            )
        except ConfirmedNoDispatchError:
            raise
        except Exception:  # noqa: BLE001 - redact injected client/provider diagnostics
            raise EvaluationBlockedError("provider outcome is ambiguous") from None
        status = response.status_code
        if not isinstance(status, int) or isinstance(status, bool):
            raise EvaluationBlockedError("provider returned an invalid HTTP status")
        if status < 200 or status >= 300:
            raise EvaluationBlockedError(f"provider returned HTTP status {status}")
        content_type = str(response.headers.get("content-type", "")).lower()
        if "text/event-stream" in content_type:
            raise EvaluationBlockedError("streamed Responses are not supported")
        if "application/json" not in content_type:
            raise EvaluationBlockedError("provider response is not JSON")
        return response


class DevelopmentExactInputTokenCountTransport:
    """Controller adapter for the admitted development-only count source."""

    provider_disconnected = False

    def __init__(self, source: DevelopmentOpenAIResponsesHttpSource) -> None:
        self.source = source

    async def count(
        self,
        *,
        model: str,
        request_payload: dict[str, Any],
        sdk_max_retries: int,
    ) -> ExactInputTokenCountResult:
        if model != "gpt-6-luna" or sdk_max_retries != 0:
            raise ConfirmedNoDispatchError("count transport policy changed")
        count = await self.source.count_input_tokens(request_payload)
        digest = hashlib.sha256(
            json.dumps(
                request_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return ExactInputTokenCountResult(count, digest)
