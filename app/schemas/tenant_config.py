"""Request/response models for staff tenant configuration (Phase 1P.3).

These are the wire contract for ``/staff/tenant-config``. Two properties are
enforced here as well as in the service, so a malformed request is rejected
before it reaches a query:

* No model has a field that could carry a widget key, its digest, a session
  token, or any integration credential. There is nowhere for one to be
  accepted, echoed, or persisted by accident.
* ``allowed_origins`` is validated as exact origins, so the HTTP path and the
  manifest path enforce identical rules.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_DISPLAY_NAME_LENGTH = 100
MAX_WELCOME_MESSAGE_LENGTH = 500
MAX_THEME_TOKEN_LENGTH = 100
MAX_ORIGINS = 20
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _validate_exact_origin(value: str) -> str:
    candidate = value.strip()
    if "*" in candidate:
        raise ValueError("wildcard origins are not supported")
    if "://" not in candidate:
        raise ValueError("must be an absolute origin, e.g. https://www.example.com")
    parts = urlsplit(candidate)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("must not include a path, query, or fragment")
    if parts.username or parts.password:
        raise ValueError("must not include credentials")
    if not parts.hostname:
        raise ValueError("must include a host")
    if candidate.endswith("/"):
        raise ValueError("must not include a trailing slash")
    if parts.scheme != "https" and parts.hostname.lower() not in _LOOPBACK_HOSTS:
        raise ValueError("must use https (http is loopback-only)")
    return candidate


class PublicChatUpdateRequest(BaseModel):
    """Staff edits to a tenant's public widget.

    Rate and session-TTL limits are intentionally not editable here; they are
    abuse controls set through the reviewed manifest.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(
        default=None,
        max_length=MAX_DISPLAY_NAME_LENGTH,
    )
    welcome_message: str | None = Field(
        default=None,
        max_length=MAX_WELCOME_MESSAGE_LENGTH,
    )
    allowed_origins: list[str] | None = None
    theme_token: str | None = Field(
        default=None,
        max_length=MAX_THEME_TOKEN_LENGTH,
    )
    enabled: bool | None = None

    @field_validator("allowed_origins")
    @classmethod
    def _origins_must_be_exact(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is None:
            return None
        if len(value) > MAX_ORIGINS:
            raise ValueError(f"at most {MAX_ORIGINS} origins are allowed")
        cleaned: list[str] = []
        for origin in value:
            candidate = _validate_exact_origin(origin)
            if candidate in cleaned:
                raise ValueError(f"duplicate origin: {candidate}")
            cleaned.append(candidate)
        return cleaned


class PublicChatSettingsResponse(BaseModel):
    id: int
    display_name: str
    welcome_message: str
    allowed_origins: list[str]
    theme_token: str | None
    enabled: bool
    max_message_length: int
    max_messages_per_minute: int
    session_ttl_hours: int
    has_widget_key: bool
    widget_key_status: str
    updated_at: str | None = None


class WidgetKeyRotationResponse(BaseModel):
    """Returned exactly once, immediately after a rotation."""

    public_widget_key: str
    warning: str


class BusinessIntegrationState(BaseModel):
    provider: str
    enabled: bool
    provider_mode: str | None
    tool_names: list[str]


class BusinessIntegrationsResponse(BaseModel):
    integrations: list[BusinessIntegrationState]


class BusinessIntegrationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class BusinessIntegrationStateResponse(BaseModel):
    provider: str
    enabled: bool
    provider_mode: str | None
