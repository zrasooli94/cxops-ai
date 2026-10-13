"""Shared helpers for Phase 1Q commercial onboarding services."""

from __future__ import annotations

import hashlib
import secrets
from urllib.parse import urlsplit

INVITATION_TOKEN_PREFIX = "inv_"
INVITATION_TOKEN_ENTROPY_BYTES = 32

# Theme tokens a tenant may pick for itself. Tenant-specific palettes (for
# example the live pilot's branded theme) are assigned by an operator through
# the reviewed manifest and are deliberately not selectable here.
SELF_SERVICE_THEME_TOKENS = frozenset({"default", "midnight", "ocean", "forest"})


class CommercialValidationError(ValueError):
    """Input that is well-formed JSON but not acceptable to the service."""


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def generate_invitation_token() -> str:
    return f"{INVITATION_TOKEN_PREFIX}{secrets.token_urlsafe(INVITATION_TOKEN_ENTROPY_BYTES)}"


def normalize_website(value: str | None) -> str | None:
    """Return a canonical ``https://host[:port]`` business website, or None.

    Accepts a bare host ("example.com") for convenience. Rejects anything that
    is not a plain http(s) URL with a dotted host, credentials, or over-long.
    """
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"}:
        raise CommercialValidationError("website must be an http(s) address")
    if parts.username or parts.password:
        raise CommercialValidationError("website must not include credentials")
    host = (parts.hostname or "").lower()
    if not host or "." not in host or any(ch.isspace() for ch in host):
        raise CommercialValidationError("website must include a valid domain")
    if len(candidate) > 255:
        raise CommercialValidationError("website is too long")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"
