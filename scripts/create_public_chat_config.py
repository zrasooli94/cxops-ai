"""Provision a tenant's public web-chat widget configuration (Phase 1P.1).

Usage:
    python -m scripts.create_public_chat_config \
        --organization-id 7 \
        --name "Acme Support Widget" \
        --welcome-message "Hi! How can we help?" \
        --origins "https://www.acme.com,https://support.acme.com"

Idempotent per organization + display name: re-running with the same name
updates the row in place and keeps the existing public widget key (so embedded
widgets keep working), unless ``--rotate-key`` is passed.

On first creation the script generates a high-entropy public widget key
(``pk_live_<192-bit hex>``), stores only its SHA-256 digest, and prints the key
exactly once. The key is public by design (it travels in customer-facing HTML)
but its digest is the only thing ever persisted. The digest is never printed.

Origin values must be whole origins (``scheme://host[:port]``), no paths, no
trailing slashes — the backend enforces exact-match against this allowlist.
"""

import argparse
import asyncio
import re
import secrets
from urllib.parse import urlsplit

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.organization import Organization
from app.models.public_chat import PublicChatConfiguration
from app.services.public_chat_service import hash_digest

log = get_logger(__name__)

WIDGET_KEY_PREFIX = "pk_live_"

_ORIGIN_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def _new_public_widget_key() -> str:
    return f"{WIDGET_KEY_PREFIX}{secrets.token_hex(24)}"


def _validate_origin(origin: str) -> str:
    if not _ORIGIN_RE.match(origin):
        raise SystemExit(
            f"error: origin {origin!r} must include a scheme (scheme://host[:port])"
        )
    parts = urlsplit(origin)
    if not parts.hostname or parts.path not in ("", "/"):
        raise SystemExit(
            f"error: origin {origin!r} must not include a path or trailing slash"
        )
    if parts.username or parts.password:
        raise SystemExit(
            f"error: origin {origin!r} must not include credentials"
        )
    return origin


def _parse_origins(raw: str | None) -> list[str]:
    if not raw:
        return []
    origins: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        origin = _validate_origin(item)
        if origin not in origins:
            origins.append(origin)
    return origins


async def upsert(
    *,
    organization_id: int,
    name: str,
    welcome_message: str,
    allowed_origins: list[str],
    enabled: bool,
    theme_token: str | None,
    max_message_length: int,
    max_messages_per_minute: int,
    session_ttl_hours: int,
    rotate_key: bool,
) -> None:
    async with AsyncSessionLocal() as db:
        organization = await db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        if organization.scalar_one_or_none() is None:
            raise SystemExit(
                f"error: organization {organization_id} does not exist"
            )

        existing = await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == organization_id,
                PublicChatConfiguration.display_name == name,
            )
        )
        config = existing.scalar_one_or_none()

        if config is None:
            widget_key = _new_public_widget_key()
            config = PublicChatConfiguration(
                organization_id=organization_id,
                public_widget_key_hash=hash_digest(widget_key),
                display_name=name,
                welcome_message=welcome_message,
                allowed_origins=allowed_origins,
                enabled=enabled,
                theme_token=theme_token,
                max_message_length=max_message_length,
                max_messages_per_minute=max_messages_per_minute,
                session_ttl_hours=session_ttl_hours,
            )
            db.add(config)
            await db.commit()
            await db.refresh(config)
            print(f"created public chat configuration '{name}' (id={config.id})")
            print(f"public widget key (store once, do not log): {widget_key}")
            return

        if rotate_key:
            widget_key = _new_public_widget_key()
            config.public_widget_key_hash = hash_digest(widget_key)

        config.display_name = name
        config.welcome_message = welcome_message
        config.allowed_origins = allowed_origins
        config.enabled = enabled
        config.theme_token = theme_token
        config.max_message_length = max_message_length
        config.max_messages_per_minute = max_messages_per_minute
        config.session_ttl_hours = session_ttl_hours
        await db.commit()
        await db.refresh(config)

        if rotate_key:
            print(f"rotated widget key for '{name}' (id={config.id})")
            print(f"public widget key (store once, do not log): {widget_key}")
        else:
            print(f"updated public chat configuration '{name}' (id={config.id}); "
                  f"public widget key unchanged")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create or update a tenant's public web-chat widget configuration. "
            "First creation prints the public widget key exactly once."
        )
    )
    parser.add_argument("--organization-id", required=True, type=int)
    parser.add_argument("--name", required=True, help="Widget display name")
    parser.add_argument(
        "--welcome-message",
        default="Hi! How can we help?",
        help="Welcome message shown inside the widget",
    )
    parser.add_argument(
        "--origins",
        default="",
        help="Comma-separated exact embedding origins, e.g. https://www.acme.com",
    )
    parser.add_argument(
        "--disable",
        action="store_true",
        help="Create/update the widget in the disabled state (default: enabled)",
    )
    parser.add_argument(
        "--theme-token",
        default=None,
        help="Opaque theme token rendered from a closed set of known mappings",
    )
    parser.add_argument(
        "--max-message-length",
        type=int,
        default=4000,
        help="Maximum inbound message length in characters (1..10000)",
    )
    parser.add_argument(
        "--max-messages-per-minute",
        type=int,
        default=20,
        help="Per-session message limit per minute (1..300)",
    )
    parser.add_argument(
        "--session-ttl-hours",
        type=int,
        default=24,
        help="Session credential lifetime in hours (1..168)",
    )
    parser.add_argument(
        "--rotate-key",
        action="store_true",
        help="Regenerate the public widget key on an existing configuration",
    )
    args = parser.parse_args()

    if args.organization_id <= 0:
        parser.error("--organization-id must be a positive integer")
    if not 1 <= args.max_message_length <= 10000:
        parser.error("--max-message-length must be between 1 and 10000")
    if not 1 <= args.max_messages_per_minute <= 300:
        parser.error("--max-messages-per-minute must be between 1 and 300")
    if not 1 <= args.session_ttl_hours <= 168:
        parser.error("--session-ttl-hours must be between 1 and 168")

    origins = _parse_origins(args.origins)

    asyncio.run(
        upsert(
            organization_id=args.organization_id,
            name=args.name,
            welcome_message=args.welcome_message,
            allowed_origins=origins,
            enabled=not args.disable,
            theme_token=args.theme_token,
            max_message_length=args.max_message_length,
            max_messages_per_minute=args.max_messages_per_minute,
            session_ttl_hours=args.session_ttl_hours,
            rotate_key=args.rotate_key,
        )
    )
    log.info(
        "public_chat_config_upserted",
        organization_id=args.organization_id,
        name=args.name,
        origin_count=len(origins),
        rotate_key=args.rotate_key,
    )


if __name__ == "__main__":
    main()