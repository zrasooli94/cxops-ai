"""One-shot OpenAI model-metadata access check for an approved local operator."""

from __future__ import annotations

import json
import ssl
from dataclasses import asdict, dataclass
from typing import Any, Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

MODEL_ID = "gpt-6-luna"
MODEL_ACCESS_ENDPOINT = f"https://api.openai.com/v1/models/{MODEL_ID}"
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_RESPONSE_BYTES = 65_536


@dataclass(frozen=True)
class ModelAccessResult:
    http_status: int | None
    metadata_accessible: bool
    error_category: str | None

    def sanitized(self) -> dict[str, Any]:
        return asdict(self)


class _Response(Protocol):
    status: int

    def read(self, amount: int = -1) -> bytes: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, *_args: object) -> None: ...


class _Opener(Protocol):
    def open(self, request: Request, *, timeout: float) -> _Response: ...


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def _one_shot_opener() -> OpenerDirector:
    # Disable environment proxies and replace urllib's redirect handler.  No
    # retry/auth/fallback handler is installed.
    return build_opener(
        ProxyHandler({}),
        _RejectRedirects(),
        HTTPSHandler(context=ssl.create_default_context()),
    )


def _http_error_category(status: int) -> str:
    if 300 <= status < 400:
        return "redirect_blocked"
    if status == 401:
        return "authentication_rejected"
    if status == 403:
        return "permission_denied"
    if status == 404:
        return "model_not_found_or_inaccessible"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "provider_server_error"
    return "http_error"


def check_gpt6_luna_model_access(
    bearer_token: str,
    *,
    opener: _Opener | None = None,
) -> ModelAccessResult:
    """Send exactly one fixed metadata GET and return no provider body."""

    if not bearer_token or any(character.isspace() for character in bearer_token):
        return ModelAccessResult(None, False, "credential_rejected_locally")
    request = Request(
        MODEL_ACCESS_ENDPOINT,
        headers={"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with (opener or _one_shot_opener()).open(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            status = response.status
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        return ModelAccessResult(exc.code, False, _http_error_category(exc.code))
    except ssl.SSLError:
        return ModelAccessResult(None, False, "tls_error")
    except TimeoutError:
        return ModelAccessResult(None, False, "timeout_uncertain")
    except URLError:
        return ModelAccessResult(None, False, "network_error_uncertain")
    except Exception:  # noqa: BLE001 - provider/local details must stay redacted
        return ModelAccessResult(None, False, "transport_error_uncertain")

    if status != 200:
        return ModelAccessResult(status, False, _http_error_category(status))
    if len(body) > MAX_RESPONSE_BYTES:
        return ModelAccessResult(status, False, "invalid_metadata")
    try:
        metadata = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ModelAccessResult(status, False, "invalid_metadata")
    accessible = (
        isinstance(metadata, dict)
        and metadata.get("id") == MODEL_ID
        and metadata.get("object") == "model"
    )
    return ModelAccessResult(
        status,
        accessible,
        None if accessible else "invalid_metadata",
    )
