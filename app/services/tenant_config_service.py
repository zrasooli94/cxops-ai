"""Staff-facing tenant configuration service (Phase 1P.3).

Read and mutate one tenant's live pilot configuration from the control center.
Every method is tenant-scoped by the ``organization_id`` the caller supplies
from their resolved tenant context - there is no code path where a client
supplies an organization id, so a staff member cannot address another tenant's
configuration.

Two invariants are enforced here rather than in the route layer, because the
onboarding CLI and the HTTP API both depend on them:

1. **The widget-key digest never leaves this module.** Responses describe
   whether a key exists; they never contain the digest, and the raw key exists
   only as the return value of :meth:`TenantConfigService.rotate_widget_key`,
   exactly once per rotation.
2. **Every mutation is audited.** There is no durable audit table in this
   schema (and Phase 1P.3 explicitly does not add a migration), so the audit
   trail is the structured log: one event per change, carrying the tenant, the
   actor, and the *field names* that changed - never their new values for
   anything sensitive, and never a key.
"""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.business_integration import BusinessIntegrationConfiguration
from app.models.public_chat import PublicChatConfiguration
from app.services.business_integration_service import BusinessIntegrationService
from app.services.public_chat_service import hash_digest
from app.tenant_onboarding.planner import (
    WIDGET_KEY_ENTROPY_BYTES,
    WIDGET_KEY_PREFIX,
    resolve_canonical_public_chat_config,
)
from app.tools.registry import business_tool_registry

log = get_logger(__name__)

# Shown to staff in place of a key they cannot retrieve. The digest is one-way,
# so an existing key genuinely cannot be displayed after the fact.
KEY_UNAVAILABLE_MESSAGE = (
    "key unavailable; rotate to generate a new one"
)

# Fields a staff member may change from the control center. Rate/TTL limits are
# deliberately excluded: they are abuse controls, and changing them is an
# onboarding-time decision that belongs in the reviewed manifest.
MUTABLE_PUBLIC_CHAT_FIELDS = frozenset(
    {
        "display_name",
        "welcome_message",
        "allowed_origins",
        "theme_token",
        "enabled",
    }
)

# Fields where an explicit JSON null means "remove the stored value" instead of
# "no change". Keep this in step with the nullable columns in
# PublicChatConfiguration: a null for anything else is a client mistake.
CLEARABLE_PUBLIC_CHAT_FIELDS = frozenset({"theme_token"})

MAX_DISPLAY_NAME_LENGTH = 100
MAX_WELCOME_MESSAGE_LENGTH = 500
MAX_THEME_TOKEN_LENGTH = 100
MAX_ORIGINS = 20


class TenantConfigError(Exception):
    pass


class TenantWidgetNotFoundError(TenantConfigError):
    pass


class AmbiguousWidgetError(TenantConfigError):
    pass


class UnsupportedProviderError(TenantConfigError):
    pass


def _validate_origins(origins: list[str]) -> list[str]:
    """Re-validate the exact-origin allowlist at the service boundary.

    The manifest loader already enforces this, but staff edits arrive over HTTP
    and must not be able to smuggle a wildcard or a path-bearing origin past
    the same rules.
    """
    from urllib.parse import urlsplit

    if len(origins) > MAX_ORIGINS:
        raise TenantConfigError(f"at most {MAX_ORIGINS} origins are allowed")

    cleaned: list[str] = []
    for origin in origins:
        candidate = origin.strip()
        if not candidate:
            continue
        if "*" in candidate:
            raise TenantConfigError("wildcard origins are not supported")
        if "://" not in candidate:
            raise TenantConfigError("origins must be absolute, e.g. https://www.example.com")
        parts = urlsplit(candidate)
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise TenantConfigError("origins must not include a path, query, or fragment")
        if parts.username or parts.password:
            raise TenantConfigError("origins must not include credentials")
        if not parts.hostname:
            raise TenantConfigError("origins must include a host")
        if candidate.endswith("/"):
            raise TenantConfigError("origins must not include a trailing slash")
        if parts.scheme != "https" and parts.hostname.lower() not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise TenantConfigError("origins must use https (http is loopback-only)")
        if candidate in cleaned:
            raise TenantConfigError(f"duplicate origin: {candidate}")
        cleaned.append(candidate)
    return cleaned


class TenantConfigService:
    @staticmethod
    async def get_public_chat(
        db: AsyncSession,
        organization_id: int,
    ) -> dict[str, Any]:
        config, total_rows = await resolve_canonical_public_chat_config(
            db,
            organization_id,
        )
        if total_rows > 1:
            raise AmbiguousWidgetError(
                f"tenant has {total_rows} widget rows; a tenant has exactly one. "
                "Resolve the extras by hand before editing configuration."
            )
        if config is None:
            raise TenantWidgetNotFoundError(
                "This tenant has no public chat widget. Onboard it with "
                "scripts/onboard_tenant.py first."
            )
        return TenantConfigService._public_chat_payload(config)

    @staticmethod
    def _public_chat_payload(config: PublicChatConfiguration) -> dict[str, Any]:
        """Build the staff-facing view. The key digest is intentionally absent."""
        return {
            "id": config.id,
            "display_name": config.display_name,
            "welcome_message": config.welcome_message,
            "allowed_origins": list(config.allowed_origins or []),
            "theme_token": config.theme_token,
            "enabled": config.enabled,
            "max_message_length": config.max_message_length,
            "max_messages_per_minute": config.max_messages_per_minute,
            "session_ttl_hours": config.session_ttl_hours,
            "has_widget_key": bool(config.public_widget_key_hash),
            "widget_key_status": KEY_UNAVAILABLE_MESSAGE,
            "updated_at": config.updated_at.isoformat() if config.updated_at else None,
        }

    @staticmethod
    async def update_public_chat(
        db: AsyncSession,
        organization_id: int,
        changes: dict[str, Any],
        *,
        actor_subject: str,
    ) -> dict[str, Any]:
        config, total_rows = await resolve_canonical_public_chat_config(
            db,
            organization_id,
        )
        if total_rows > 1:
            raise AmbiguousWidgetError(
                f"tenant has {total_rows} widget rows; refusing to guess which to edit"
            )
        if config is None:
            raise TenantWidgetNotFoundError("This tenant has no public chat widget.")

        unknown = sorted(set(changes) - MUTABLE_PUBLIC_CHAT_FIELDS)
        if unknown:
            raise TenantConfigError(
                "fields cannot be changed here: " + ", ".join(unknown)
            )
        if not changes:
            return TenantConfigService._public_chat_payload(config)

        changed: list[str] = []

        if "display_name" in changes:
            display_name = _require_bounded_str(
                changes["display_name"],
                "display_name",
                MAX_DISPLAY_NAME_LENGTH,
            )
            if config.display_name != display_name:
                config.display_name = display_name
                changed.append("display_name")

        if "welcome_message" in changes:
            welcome_message = _require_bounded_str(
                changes["welcome_message"],
                "welcome_message",
                MAX_WELCOME_MESSAGE_LENGTH,
            )
            if config.welcome_message != welcome_message:
                config.welcome_message = welcome_message
                changed.append("welcome_message")

        if "allowed_origins" in changes:
            raw = changes["allowed_origins"]
            if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
                raise TenantConfigError("allowed_origins must be a list of strings")
            allowed_origins = _validate_origins(list(raw))
            if list(config.allowed_origins or []) != allowed_origins:
                # An empty allowlist stops every future session but leaves
                # existing sessions and history intact: that is the kill switch.
                config.allowed_origins = allowed_origins
                changed.append("allowed_origins")

        if "theme_token" in changes:
            raw = changes["theme_token"]
            theme_token = (
                None
                if raw is None
                else _require_bounded_str(
                    raw,
                    "theme_token",
                    MAX_THEME_TOKEN_LENGTH,
                )
            )
            if config.theme_token != theme_token:
                config.theme_token = theme_token
                changed.append("theme_token")

        if "enabled" in changes:
            raw = changes["enabled"]
            if not isinstance(raw, bool):
                raise TenantConfigError("enabled must be true or false")
            if config.enabled != raw:
                config.enabled = raw
                changed.append("enabled")

        await db.commit()
        await db.refresh(config)

        # Field names only: never the new values, so a welcome-message edit
        # cannot put customer-visible copy into the log pipeline.
        log.info(
            "tenant_public_chat_config_updated",
            organization_id=organization_id,
            actor_subject=actor_subject,
            changed_fields=sorted(changed),
            enabled=config.enabled,
            origin_count=len(config.allowed_origins or []),
        )
        return TenantConfigService._public_chat_payload(config)

    @staticmethod
    async def rotate_widget_key(
        db: AsyncSession,
        organization_id: int,
        *,
        actor_subject: str,
    ) -> dict[str, Any]:
        """Mint a new widget key and return it exactly once.

        The previous digest is overwritten in the same transaction, so the old
        key stops resolving the moment this commits. There is no grace period by
        design: a rotation is the response to a leaked key.
        """
        config, total_rows = await resolve_canonical_public_chat_config(
            db,
            organization_id,
        )
        if total_rows > 1:
            raise AmbiguousWidgetError(
                f"tenant has {total_rows} widget rows; refusing to rotate blindly"
            )
        if config is None:
            raise TenantWidgetNotFoundError("This tenant has no public chat widget.")

        widget_key = f"{WIDGET_KEY_PREFIX}{secrets.token_hex(WIDGET_KEY_ENTROPY_BYTES)}"
        config.public_widget_key_hash = hash_digest(widget_key)
        await db.commit()
        await db.refresh(config)

        log.info(
            "tenant_public_chat_key_rotated",
            organization_id=organization_id,
            actor_subject=actor_subject,
            configuration_id=config.id,
        )

        # The only path in the codebase that returns raw key material, and only
        # at the moment of rotation.
        return {
            "public_widget_key": widget_key,
            "warning": (
                "This key is shown once and cannot be retrieved later. "
                "Update the website embed immediately; the previous key stopped working."
            ),
        }

    @staticmethod
    async def list_business_integrations(
        db: AsyncSession,
        organization_id: int,
    ) -> dict[str, Any]:
        """List providers that exist in the registry with this tenant's state."""
        result = await db.execute(
            select(BusinessIntegrationConfiguration).where(
                BusinessIntegrationConfiguration.organization_id == organization_id,
            )
        )
        rows = {row.provider: row for row in result.scalars().all()}

        payload: list[dict[str, Any]] = []
        for definition in business_tool_registry.definitions():
            if definition.provider is None:
                continue
            row = rows.get(definition.provider)
            payload.append(
                {
                    "provider": definition.provider,
                    "enabled": bool(row.enabled) if row is not None else False,
                    "provider_mode": (row.config_json or {}).get("provider_mode")
                    if row is not None
                    else None,
                    "tool_names": sorted(
                        d.name for d in business_tool_registry.definitions()
                        if d.provider == definition.provider
                    ),
                }
            )
        return {"integrations": payload}

    @staticmethod
    async def set_business_integration(
        db: AsyncSession,
        organization_id: int,
        provider: str,
        enabled: bool,
        *,
        actor_subject: str,
    ) -> dict[str, Any]:
        known_providers = {
            d.provider for d in business_tool_registry.definitions() if d.provider
        }
        if provider not in known_providers:
            raise UnsupportedProviderError(
                f"unknown provider {provider!r}; known providers: "
                + ", ".join(sorted(known_providers))
            )

        # set_provider_enabled only flushes; the commit is this service's job so
        # the audit event and the row move together.
        row = await BusinessIntegrationService.set_provider_enabled(
            db,
            organization_id,
            provider,
            enabled,
        )
        await db.commit()
        await db.refresh(row)

        log.info(
            "tenant_business_integration_updated",
            organization_id=organization_id,
            actor_subject=actor_subject,
            provider=provider,
            enabled=enabled,
        )
        return {
            "provider": provider,
            "enabled": enabled,
            "provider_mode": (row.config_json or {}).get("provider_mode"),
        }


def _require_bounded_str(value: Any, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise TenantConfigError(f"{field} must be a string")
    candidate = value.strip()
    if not candidate:
        raise TenantConfigError(f"{field} must not be empty")
    if len(candidate) > max_length:
        raise TenantConfigError(
            f"{field} must be at most {max_length} characters (got {len(candidate)})"
        )
    return candidate
