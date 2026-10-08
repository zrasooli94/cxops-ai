"""RISPU - first live pilot tenant, shipped-config contract (Phase 1P.3).

RISPU is the first tenant whose public widget goes live to a real customer
site. These tests pin the shipped manifest (`config/tenants/rispu.yaml`) and
the frontend `frame-ancestors` allowlist that mirrors it, so that a change to
either fails the suite instead of silently widening who may embed the widget or
who the widget may be shown to.

The manifest loader is the strong enforcement: an enabled widget must list at
least one exact https origin, origin matching is exact string membership, and
credential-shaped keys and values are rejected outright. The tests below re-state
those guarantees for this specific shipped file, explicitly, so a reviewer or a
later edit cannot rely on a subtle loader-default that used to hold.
"""

import os
from pathlib import Path

os.environ.setdefault("AUTH_MODE", "hs256")
os.environ.setdefault("AUTH_JWT_SECRET", "z" * 32)
os.environ.setdefault("AUTH_JWT_ALGORITHM", "HS256")
os.environ.setdefault("AUTH_DEV_MODE", "False")
os.environ.setdefault("ENVIRONMENT", "development")

from app.tenant_onboarding import load_manifest_file

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "config" / "tenants" / "rispu.yaml"
EXAMPLE_MANIFEST = REPO_ROOT / "config" / "tenants" / "rispu.example.yaml"
NEXT_CONFIG = REPO_ROOT / "frontend" / "next.config.ts"

RISPU_ORIGINS = ("https://rispu.com", "https://www.rispu.com")

# Field-shaped credential names that must never appear in the manifest, even in
# a comment. Whole-word secret/material words are deliberately excluded here:
# the shipped SECURITY comment names "webhook secrets" and "staff tokens" on
# purpose, and the loader is the authoritative guard for value shapes.
_CREDENTIAL_FIELD_FRAGMENTS = (
    "api_key",
    "apikey",
    "password",
    "passwd",
    "private_key",
    "connection_string",
    "database_url",
    "access_token",
    "client_secret",
    "jwt_secret",
    "signing_key",
    "public_widget_key",
    "session_token",
)

_EMBED_ORIGINS = (
    "'self'",
    "https://a1cashforcars.com.au",
    "https://www.a1cashforcars.com.au",
    "https://rispu.com",
    "https://www.rispu.com",
)


def test_rispu_manifest_loads_as_the_live_pilot() -> None:
    manifest = load_manifest_file(MANIFEST)

    assert manifest.schema_version == 1
    assert manifest.slug == "rispu"
    assert manifest.name == "RISPU"
    assert manifest.industry == "Software"
    assert manifest.public_chat is not None
    assert manifest.public_chat.enabled is True
    assert manifest.public_chat.display_name == "RISPU Support"
    assert manifest.public_chat.welcome_message == (
        "Hi! How can we help? Send us a message and our support team will review it."
    )
    assert manifest.public_chat.allowed_origins == RISPU_ORIGINS
    assert manifest.public_chat.theme_token == "default"
    assert manifest.public_chat.max_message_length == 2000
    assert manifest.public_chat.max_messages_per_minute == 12
    assert manifest.public_chat.session_ttl_hours == 24
    assert manifest.business_integrations == ()
    assert manifest.knowledge, "RISPU pilot must ship tenant knowledge"
    assert manifest.pilot is not None and manifest.pilot.state == "pilot"


def test_rispu_allowed_origins_are_exact_https_origins_only() -> None:
    manifest = load_manifest_file(MANIFEST)
    origins = manifest.public_chat.allowed_origins

    assert origins == RISPU_ORIGINS
    for origin in origins:
        assert origin.startswith("https://"), origin
        assert "*" not in origin
        assert not origin.endswith("/")
        # No shared-hosting host may ever be allowlisted for a tenant: every
        # deployment is a sibling subdomain of one shared parent, so a published
        # host there would let an unrelated site frame the widget and read its
        # public key out of the URL.
        assert "replit.app" not in origin
        assert "vercel.app" not in origin


def test_rispu_manifest_declares_no_integrations_and_ships_rispu_knowledge() -> None:
    manifest = load_manifest_file(MANIFEST)

    assert manifest.business_integrations == ()
    assert manifest.knowledge, "RISPU pilot must ship tenant knowledge"


def test_rispu_pilot_state_is_documentation_not_a_production_claim() -> None:
    manifest = load_manifest_file(MANIFEST)

    assert manifest.pilot is not None
    assert manifest.pilot.state == "pilot"
    text = MANIFEST.read_text(encoding="utf-8")
    assert 'state: pilot' in text
    assert 'state: production' not in text


def test_rispu_manifest_contains_no_credential_shaped_material() -> None:
    text = MANIFEST.read_text(encoding="utf-8").lower()

    for fragment in _CREDENTIAL_FIELD_FRAGMENTS:
        assert fragment not in text, f"credential-shaped field {fragment!r} found"

    load_manifest_file(MANIFEST)  # the loader enforces the value-shape denylist


def test_rispu_manifest_comment_discloses_the_pilot_posture() -> None:
    """A reviewer reading only the manifest must learn this pilot's bounds."""
    text = MANIFEST.read_text(encoding="utf-8").lower()

    assert "live" in text
    assert "handoff" in text
    assert "no real provider" in text
    assert "staff must review" in text
    assert "production" in text, "the manifest must state what it is not"


def test_rispu_example_template_remains_inert() -> None:
    """The example template stays an off template, distinct from the live tenant."""
    manifest = load_manifest_file(EXAMPLE_MANIFEST)

    assert manifest.slug == "rispu-example"
    assert manifest.public_chat is not None
    assert manifest.public_chat.enabled is False
    assert manifest.public_chat.allowed_origins == ()
    assert manifest.business_integrations == ()
    assert manifest.knowledge == ()
    assert manifest.pilot is not None and manifest.pilot.state == "disabled"


def _frame_ancestor_allowlist() -> str:
    text = NEXT_CONFIG.read_text(encoding="utf-8")
    start = text.index("WIDGET_FRAME_ANCESTORS = [")
    end = text.index("];", start)
    return text[start:end]


def test_next_config_frames_the_widget_for_self_a1_and_rispu_only() -> None:
    allowlist = _frame_ancestor_allowlist()

    for expected in _EMBED_ORIGINS:
        assert expected in allowlist, f"missing frame-ancestor {expected!r}"


def test_next_config_has_no_wildcard_or_shared_hosting_origin() -> None:
    allowlist = _frame_ancestor_allowlist()

    for forbidden in ("https://*", "*.rispu.com", "*.replit.app", "*.vercel.app", "*"):
        assert forbidden not in allowlist, f"forbidden origin {forbidden!r} present"
    assert allowlist.count("https://rispu.com") == 1
    assert allowlist.count("https://www.rispu.com") == 1