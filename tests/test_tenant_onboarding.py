"""Phase 1P.3 — declarative tenant onboarding.

Covers the properties a live pilot depends on, at three levels:

**Manifest loading (no database).** Unknown fields, credential-shaped keys and
values, and malformed exact origins are all rejected, and every problem in a
file is reported in one pass. The two shipped manifests are validated too, so a
typo in ``config/tenants/`` fails the suite rather than a deploy.

**Planning (read-only).** A plan never writes. It reports CREATE / UPDATE /
UNCHANGED / DISABLE / ERROR, it does not mint a key during a dry run, and it
refuses to guess when a tenant somehow has more than one widget row.

**Apply (transactional).** Onboarding is idempotent and preserves the existing
widget key; only an explicit rotation changes it, and the old key stops
resolving the moment the rotation commits. Tenants are isolated from each
other, knowledge ingestion is content-deduplicated and never touches another
tenant's documents, and disabling one tenant's provider or widget leaves every
other tenant working.
"""

import asyncio
import copy
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, or_, select

os.environ.setdefault("AUTH_MODE", "hs256")
os.environ.setdefault("AUTH_JWT_SECRET", "z" * 32)
os.environ.setdefault("AUTH_JWT_ALGORITHM", "HS256")
os.environ.setdefault("AUTH_DEV_MODE", "False")
os.environ.setdefault("ENVIRONMENT", "development")

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.base import Base
from app.models.business_integration import BusinessIntegrationConfiguration
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.organization import Organization
from app.models.public_chat import PublicChatConfiguration
from app.services.business_integration_service import BusinessIntegrationService
from app.services.embedding_service import embedding_service
from app.services.public_chat_service import (
    PublicChatConfigurationNotFoundError,
    hash_digest,
    public_chat_service,
)
from app.tenant_onboarding import (
    ManifestError,
    OnboardingConflictError,
    apply_plan,
    build_embed_snippet,
    build_plan,
    load_manifest_file,
    parse_manifest,
    planner,
)
from app.tenant_onboarding.manifest import SecretMaterialError
from app.tenant_onboarding.planner import (
    ACTION_CREATE,
    ACTION_DISABLE,
    ACTION_ERROR,
    ACTION_UNCHANGED,
    ACTION_UPDATE,
    WIDGET_KEY_PREFIX,
    generate_public_widget_key,
    resolve_canonical_public_chat_config,
)
from app.tools.registry import business_tool_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = REPO_ROOT / "config" / "tenants"
A1_MANIFEST = MANIFEST_DIR / "a1-cash-for-cars.yaml"
RISPU_MANIFEST = MANIFEST_DIR / "rispu.example.yaml"

ORG_NAME_PREFIX = "Phase1P3-test-"
ORG_SLUG_PREFIXES = ("acme", "alpha", "beta", "keep", "stop", "target", "unrelated")
PROVIDER_A1 = "a1_cash_for_cars"
# Derived from the live registry so a registry change fails loudly instead of
# letting a hardcoded copy drift out of sync with reality.
A1_TOOL_NAMES = frozenset(
    d.name
    for d in business_tool_registry.definitions()
    if d.provider == PROVIDER_A1
)
assert A1_TOOL_NAMES, "A1 registry entry exposes no tools"

_PURGE_TABLES = [
    "public_chat_configurations",
    "business_integration_configurations",
    "knowledge_chunks",
    "knowledge_documents",
    "organization_memberships",
    "organizations",
]


# ----------------------------------------------------------------------
# Manifest fixtures
# ----------------------------------------------------------------------


def _manifest_document(**overrides) -> dict:
    document = {
        "schema_version": 1,
        "tenant": {"slug": "acme", "name": "Acme Motors", "industry": "Retail"},
        "public_chat": {
            "enabled": True,
            "display_name": "Acme Support",
            "welcome_message": "Hi!",
            "allowed_origins": ["https://www.acme.example"],
            "theme_token": "acme-dark",
            "max_message_length": 1500,
            "max_messages_per_minute": 9,
            "session_ttl_hours": 12,
        },
        "business_integrations": [
            {"provider": PROVIDER_A1, "enabled": True, "provider_mode": "local_demo"},
        ],
        "knowledge": [
            {
                "source_id": "acme-faq",
                "version": 1,
                "title": "Acme FAQ",
                "content": "Acme answers general questions during business hours.",
                "department": "support",
            },
        ],
        "pilot": {"state": "local_demo"},
    }
    document.update(overrides)
    return document


def _unique_slug(prefix: str = "acme") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _renamed_name(slug: str) -> str:
    return f"{ORG_NAME_PREFIX}{slug}-renamed"


def _tenant_block(slug: str, **extra) -> dict:
    """A tenant block with a collision-proof display name.

    ``organizations.name`` is UNIQUE database-wide and the test database is
    shared with every other suite, so a friendly literal like "Created" would
    eventually collide with a seeded organization. The name is derived from the
    slug, which is unique by construction.
    """
    return {"slug": slug, "name": f"{ORG_NAME_PREFIX}{slug}", **extra}


# ----------------------------------------------------------------------
# Manifest validation (no database)
# ----------------------------------------------------------------------


def test_shipped_a1_manifest_is_valid():
    manifest = load_manifest_file(A1_MANIFEST)

    assert manifest.slug == "a1-cash-for-cars"
    assert manifest.public_chat is not None
    assert manifest.public_chat.enabled is True
    # A1's real production origins. Ordered apex-first, matched byte-for-byte
    # by the origin validator; the retired *.example placeholder must not return.
    assert manifest.public_chat.allowed_origins == (
        "https://a1cashforcars.com.au",
        "https://www.a1cashforcars.com.au",
    )
    # A branding token is not a credential and must survive the secret scan.
    assert manifest.public_chat.theme_token == "a1-dark"
    assert [i.provider for i in manifest.business_integrations] == [PROVIDER_A1]
    assert manifest.business_integrations[0].provider_mode == "local_demo"
    assert manifest.pilot is not None and manifest.pilot.state == "pilot"
    assert [d.source_id for d in manifest.knowledge] == [
        "a1-pilot-overview",
        "a1-pilot-required-documents",
        "a1-pilot-handoff-policy",
    ]


def test_shipped_riskpu_template_is_valid_and_inert():
    manifest = load_manifest_file(RISPU_MANIFEST)

    assert manifest.slug == "rispu-example"
    assert manifest.public_chat is not None
    # Inert: disabled widget, no origins, no A1 surface of any kind.
    assert manifest.public_chat.enabled is False
    assert manifest.public_chat.allowed_origins == ()
    assert manifest.business_integrations == ()
    assert manifest.knowledge == ()
    assert manifest.pilot is not None and manifest.pilot.state == "disabled"


def test_theme_token_is_not_treated_as_a_secret():
    manifest = parse_manifest(_manifest_document())

    assert manifest.public_chat is not None
    assert manifest.public_chat.theme_token == "acme-dark"


def test_grounded_auto_reply_defaults_to_false_when_omitted():
    """Every tenant is opt-in: omitting the field must leave auto-reply off."""
    manifest = parse_manifest(_manifest_document())

    assert manifest.public_chat is not None
    assert manifest.public_chat.grounded_auto_reply_enabled is False


def test_grounded_auto_reply_true_is_parsed():
    document = _manifest_document()
    document["public_chat"]["grounded_auto_reply_enabled"] = True

    manifest = parse_manifest(document)

    assert manifest.public_chat is not None
    assert manifest.public_chat.grounded_auto_reply_enabled is True


def test_grounded_auto_reply_false_is_parsed():
    document = _manifest_document()
    document["public_chat"]["grounded_auto_reply_enabled"] = False

    manifest = parse_manifest(document)

    assert manifest.public_chat is not None
    assert manifest.public_chat.grounded_auto_reply_enabled is False


@pytest.mark.parametrize("value", ["true", 1, "yes", None])
def test_grounded_auto_reply_rejects_non_boolean_values(value):
    """YAML strings like ``true`` must not be silently coerced to on."""
    document = _manifest_document()
    document["public_chat"]["grounded_auto_reply_enabled"] = value

    with pytest.raises(ManifestError) as excinfo:
        parse_manifest(document)

    joined = " ".join(excinfo.value.problems)
    assert "grounded_auto_reply_enabled" in joined, joined
    assert "must be true or false" in joined, joined


def test_shipped_a1_manifest_does_not_enable_grounded_auto_reply():
    """A1 stays a human-approved pilot; only RISPU opts in."""
    manifest = load_manifest_file(A1_MANIFEST)

    assert manifest.public_chat is not None
    assert manifest.public_chat.grounded_auto_reply_enabled is False


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        (lambda d: d.update(unexpected="x"), "unknown field"),
        (
            lambda d: d["public_chat"].update(allowed_origin=["https://a.example"]),
            "unknown field",
        ),
        (lambda d: d["tenant"].update(extra=1), "unknown field"),
        (
            lambda d: d["business_integrations"][0].update(surprise=True),
            "unknown field",
        ),
        (lambda d: d["knowledge"][0].update(surprise=True), "unknown field"),
        (lambda d: d.update(schema_version=2), "unsupported schema_version"),
        (lambda d: d["tenant"].update(slug="Not_A_Slug"), "kebab-case"),
        (lambda d: d["public_chat"].update(allowed_origins=[]), "at least one"),
        (
            lambda d: d["public_chat"].update(allowed_origins=["https://a.example/x"]),
            "must not include a path",
        ),
        (
            lambda d: d["public_chat"].update(allowed_origins=["https://*.a.example"]),
            "wildcard",
        ),
        (
            lambda d: d["public_chat"].update(allowed_origins=["https://a.example/"]),
            "trailing slash",
        ),
        (
            lambda d: d["public_chat"].update(allowed_origins=["http://a.example"]),
            "https",
        ),
        (
            lambda d: d["public_chat"].update(session_ttl_hours=9999),
            "between 1 and 168",
        ),
        (
            lambda d: d["public_chat"].update(max_messages_per_minute=0),
            "between 1 and 300",
        ),
        (
            lambda d: d["business_integrations"][0].update(provider_mode="live"),
            "unknown provider_mode",
        ),
        (lambda d: d.update(pilot={"state": "live"}), "must be one of"),
    ],
)
def test_manifest_rejects_bad_input(mutation, expected_fragment):
    document = _manifest_document()
    mutation(document)

    with pytest.raises(ManifestError) as excinfo:
        parse_manifest(document)

    joined = " ".join(excinfo.value.problems)
    assert expected_fragment in joined, joined


def test_manifest_rejects_secret_shaped_keys():
    document = _manifest_document()
    document["public_chat"]["api_key"] = "whatever"

    with pytest.raises(ManifestError) as excinfo:
        parse_manifest(document)

    assert "credential" in " ".join(excinfo.value.problems)


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://user:pass@db.example.com:5432/cxops",
        "sk-abcdefghijklmnopqrstuvwx",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop",
        "Bearer abcdefghijklmnopqrst",
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_manifest_rejects_secret_shaped_values(value):
    document = _manifest_document()
    document["tenant"]["industry"] = value

    with pytest.raises(SecretMaterialError) as excinfo:
        parse_manifest(document)

    # The offending value must never be echoed back in the error.
    assert value not in " ".join(excinfo.value.problems)
    assert "credential material" in " ".join(excinfo.value.problems)


def test_manifest_reports_every_problem_in_one_pass():
    document = _manifest_document()
    document["nope"] = 1
    document["tenant"]["slug"] = "BAD"
    document["public_chat"]["allowed_origins"] = ["https://*.a.example"]

    with pytest.raises(ManifestError) as excinfo:
        parse_manifest(document)

    assert len(excinfo.value.problems) >= 3


def test_manifest_optional_sections_may_be_omitted():
    document = {
        "schema_version": 1,
        "tenant": {"slug": "minimal", "name": "Minimal"},
    }

    manifest = parse_manifest(document)

    assert manifest.public_chat is None
    assert manifest.business_integrations == ()
    assert manifest.knowledge == ()
    assert manifest.pilot is None


def test_load_manifest_file_rejects_missing_file():
    with pytest.raises(ManifestError):
        load_manifest_file(MANIFEST_DIR / "does-not-exist.yaml")


# ----------------------------------------------------------------------
# Embed snippet
# ----------------------------------------------------------------------


def test_embed_snippet_targets_the_widget_route():
    snippet = build_embed_snippet(
        "https://app.example.com/",
        f"{WIDGET_KEY_PREFIX}deadbeef",
        'Acme "Auto"',
    )

    assert "https://app.example.com/chat/embed?key=pk_live_deadbeef" in snippet
    assert "<iframe" in snippet and "</iframe>" in snippet
    assert "loading=\"lazy\"" in snippet
    # The display name lands in a title attribute, so a quote is escaped.
    assert "&quot;" in snippet


# ----------------------------------------------------------------------
# Database fixtures
# ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture(scope="module", autouse=True)
def _namespace_isolated():
    asyncio.run(_purge())
    yield
    asyncio.run(_purge())


async def _purge() -> None:
    async with AsyncSessionLocal() as session:
        conditions = [Organization.name.like(f"{ORG_NAME_PREFIX}%")]
        conditions.extend(
            Organization.external_id.like(f"{prefix}-%")
            for prefix in ORG_SLUG_PREFIXES
        )
        org_ids = [
            org_id
            for (org_id,) in await session.execute(
                select(Organization.id).where(or_(*conditions))
            )
        ]
        if org_ids:
            for table_name in _PURGE_TABLES:
                table = Base.metadata.tables.get(table_name)
                if table is not None and "organization_id" in table.columns:
                    await session.execute(
                        delete(table).where(table.c.organization_id.in_(org_ids))
                    )
            await session.execute(
                delete(Organization).where(Organization.id.in_(org_ids))
            )
        await session.commit()


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    """Knowledge ingestion must never reach OpenAI in tests."""

    # The pgvector column is fixed at settings.embedding_dimensions (1536), so a
    # short stub vector would fail at INSERT rather than at a fake boundary.
    dimensions = settings.embedding_dimensions

    async def _embed_texts(texts: list[str]) -> list[list[float]]:
        return [[0.0] * dimensions for _ in texts]

    async def _embed_one(text: str) -> list[float]:
        return [0.0] * dimensions

    monkeypatch.setattr(embedding_service, "embed_documents", _embed_texts)
    monkeypatch.setattr(embedding_service, "embed_text", _embed_one)


# ----------------------------------------------------------------------
# Planning is read-only
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_plan_does_not_write(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))

    plan = await build_plan(db, manifest)

    assert plan.has_errors is False
    assert plan.organization_exists is False
    actions = {action.target: action.action for action in plan.actions}
    assert actions["organization"] == ACTION_CREATE
    assert actions["public_chat"] == ACTION_CREATE
    assert actions[f"business_integration:{PROVIDER_A1}"] == ACTION_CREATE
    assert actions["knowledge:acme-faq"] == ACTION_CREATE

    # Nothing may exist yet.
    existing = await db.execute(
        select(Organization).where(Organization.external_id == slug)
    )
    assert existing.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_dry_run_mints_no_key(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    rows_before = await db.execute(select(PublicChatConfiguration))
    before = len(rows_before.all())
    plan = await build_plan(db, manifest)

    # The plan knows a key *will* be created, but has not produced one.
    assert plan.mints_widget_key is True
    assert not hasattr(plan, "public_widget_key")

    rows = await db.execute(select(PublicChatConfiguration))
    assert len(rows.all()) == before, "a dry run must not create a widget row"


# ----------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------


async def _onboard(db, manifest) -> tuple[object, str]:
    plan = await build_plan(db, manifest)
    result = await apply_plan(db, manifest, plan)
    return result, plan


@pytest.mark.asyncio
async def test_apply_creates_tenant_widget_provider_and_knowledge(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))

    result, _plan = await _onboard(db, manifest)

    assert result.widget_key_created is True
    assert result.public_widget_key.startswith(WIDGET_KEY_PREFIX)
    assert result.knowledge_ingested == 1

    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    

    config, rows = await resolve_canonical_public_chat_config(db, organization.id)
    assert rows == 1
    assert config is not None
    assert config.enabled is True
    assert config.allowed_origins == ["https://www.acme.example"]
    assert config.max_messages_per_minute == 9
    # Only the digest is stored; the raw key is never persisted.
    assert config.public_widget_key_hash == hash_digest(result.public_widget_key)
    assert result.public_widget_key not in config.public_widget_key_hash

    provider = await BusinessIntegrationService.get_config(
        db,
        organization.id,
        PROVIDER_A1,
    )
    assert provider is not None
    assert provider.enabled is True
    assert (provider.config_json or {}).get("provider_mode") == "local_demo"

    documents = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.organization_id == organization.id
            )
        )
    ).scalars().all()
    assert len(documents) == 1
    assert documents[0].metadata_json["onboarding_source_id"] == "acme-faq"
    assert documents[0].metadata_json["onboarding_version"] == 1
    assert documents[0].source == "tenant-onboarding:acme-faq"


@pytest.mark.asyncio
async def test_apply_is_idempotent_and_preserves_the_key(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))

    first, _ = await _onboard(db, manifest)
    second_plan = await build_plan(db, manifest)

    assert second_plan.is_noop, second_plan.describe()
    assert all(a.action == ACTION_UNCHANGED for a in second_plan.actions)

    second = await apply_plan(db, manifest, second_plan)
    assert second.public_widget_key is None
    assert second.widget_key_created is False
    assert second.widget_key_rotated is False
    assert second.knowledge_ingested == 0
    assert second.knowledge_duplicates == 1

    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    config, _rows = await resolve_canonical_public_chat_config(db, organization.id)
    # The original key still works after a no-op re-apply.
    assert config is not None
    assert config.public_widget_key_hash == hash_digest(first.public_widget_key)

    resolved = await public_chat_service.resolve_config(db, first.public_widget_key)
    assert resolved.id == config.id


@pytest.mark.asyncio
async def test_reapply_updates_changed_fields_only(db):
    slug = _unique_slug()
    first_manifest = parse_manifest(
        _manifest_document(tenant=_tenant_block(slug))
    )
    first, _ = await _onboard(db, first_manifest)

    second_document = _manifest_document(tenant=_tenant_block(slug))
    second_document["public_chat"]["welcome_message"] = "Hello there!"
    second_document["public_chat"]["allowed_origins"] = [
        "https://www.acme.example",
        "https://help.acme.example",
    ]
    second_manifest = parse_manifest(second_document)

    plan = await build_plan(db, second_manifest)
    action = next(a for a in plan.actions if a.target == "public_chat")
    assert action.action == ACTION_UPDATE
    assert set(action.fields) == {"welcome_message", "allowed_origins"}
    # A configuration update must never be a key rotation.
    assert plan.rotates_widget_key is False

    result = await apply_plan(db, second_manifest, plan)
    assert result.public_widget_key is None
    assert result.widget_key_rotated is False

    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    config, _ = await resolve_canonical_public_chat_config(db, organization.id)
    assert config is not None
    assert config.welcome_message == "Hello there!"
    assert config.public_widget_key_hash == hash_digest(first.public_widget_key)


@pytest.mark.asyncio
async def test_apply_creates_widget_with_grounded_auto_reply_from_manifest(db):
    """A create writes the opt-in flag verbatim; the default remains off."""
    slug = _unique_slug()
    document = _manifest_document(tenant=_tenant_block(slug))
    document["public_chat"]["grounded_auto_reply_enabled"] = True

    result, _ = await _onboard(db, parse_manifest(document))

    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    config, _ = await resolve_canonical_public_chat_config(db, organization.id)
    assert config is not None
    assert config.grounded_auto_reply_enabled is True
    # Creating the widget still mints exactly one key, never a rotation.
    assert result.widget_key_created is True
    assert result.widget_key_rotated is False
    assert config.public_widget_key_hash == hash_digest(result.public_widget_key)


@pytest.mark.asyncio
async def test_apply_defaults_grounded_auto_reply_to_false(db):
    """A manifest that omits the flag must not opt the tenant in."""
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))

    await _onboard(db, manifest)

    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    config, _ = await resolve_canonical_public_chat_config(db, organization.id)
    assert config is not None
    assert config.grounded_auto_reply_enabled is False


@pytest.mark.asyncio
async def test_reapply_toggling_grounded_auto_reply_is_the_only_field_change(db):
    """Flipping the flag is a narrow config UPDATE, not a key rotation."""
    slug = _unique_slug()
    first_manifest = parse_manifest(
        _manifest_document(tenant=_tenant_block(slug))
    )
    first, _ = await _onboard(db, first_manifest)

    second_document = _manifest_document(tenant=_tenant_block(slug))
    second_document["public_chat"]["grounded_auto_reply_enabled"] = True
    second_manifest = parse_manifest(second_document)

    plan = await build_plan(db, second_manifest)
    action = next(a for a in plan.actions if a.target == "public_chat")
    assert action.action == ACTION_UPDATE
    assert set(action.fields) == {"grounded_auto_reply_enabled"}
    assert plan.rotates_widget_key is False

    result = await apply_plan(db, second_manifest, plan)
    assert result.public_widget_key is None
    assert result.widget_key_created is False
    assert result.widget_key_rotated is False

    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    config, _ = await resolve_canonical_public_chat_config(db, organization.id)
    assert config is not None
    assert config.grounded_auto_reply_enabled is True
    # The update must not disturb the key or the origin allowlist.
    assert config.public_widget_key_hash == hash_digest(first.public_widget_key)
    assert config.allowed_origins == ["https://www.acme.example"]


@pytest.mark.asyncio
async def test_reapplying_with_the_flag_set_is_a_pure_no_op(db):
    """Once the flag matches, re-apply reports UNCHANGED, not UPDATE."""
    slug = _unique_slug()
    document = _manifest_document(tenant=_tenant_block(slug))
    document["public_chat"]["grounded_auto_reply_enabled"] = True
    manifest = parse_manifest(document)

    first, _ = await _onboard(db, manifest)
    second_plan = await build_plan(db, manifest)

    assert second_plan.is_noop, second_plan.describe()
    assert all(a.action == ACTION_UNCHANGED for a in second_plan.actions)

    second = await apply_plan(db, manifest, second_plan)
    assert second.public_widget_key is None
    assert second.widget_key_created is False
    assert second.widget_key_rotated is False

    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    config, _ = await resolve_canonical_public_chat_config(db, organization.id)
    assert config is not None
    assert config.grounded_auto_reply_enabled is True
    assert config.public_widget_key_hash == hash_digest(first.public_widget_key)


@pytest.mark.asyncio
async def test_dry_run_then_apply_creates_exactly_one_widget(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))

    for _ in range(3):
        plan = await build_plan(db, manifest)
        assert plan.has_errors is False

    result = await apply_plan(db, manifest, plan)

    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    _config, rows = await resolve_canonical_public_chat_config(db, organization.id)
    assert rows == 1
    assert result.widget_key_created is True


# ----------------------------------------------------------------------
# Key lifecycle
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotation_invalidates_the_previous_key_immediately(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    first, _ = await _onboard(db, manifest)
    old_key = first.public_widget_key

    plan = await build_plan(db, manifest, rotate_widget_key=True)
    action = next(a for a in plan.actions if a.target == "public_chat")
    assert action.action == ACTION_UPDATE
    assert plan.rotates_widget_key is True

    result = await apply_plan(db, manifest, plan, rotate_widget_key=True)
    new_key = result.public_widget_key

    assert new_key != old_key
    assert new_key.startswith(WIDGET_KEY_PREFIX)
    assert result.widget_key_rotated is True

    # The old key stops resolving the moment the rotation commits.
    with pytest.raises(PublicChatConfigurationNotFoundError):
        await public_chat_service.resolve_config(db, old_key)

    resolved = await public_chat_service.resolve_config(db, new_key)
    assert resolved.organization_id == result.organization_id


@pytest.mark.asyncio
async def test_plain_reapply_after_rotation_keeps_the_new_key(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    first, _ = await _onboard(db, manifest)
    rotated = await apply_plan(
        db,
        manifest,
        await build_plan(db, manifest, rotate_widget_key=True),
        rotate_widget_key=True,
    )

    await apply_plan(db, manifest, await build_plan(db, manifest))

    with pytest.raises(PublicChatConfigurationNotFoundError):
        await public_chat_service.resolve_config(db, first.public_widget_key)
    resolved = await public_chat_service.resolve_config(db, rotated.public_widget_key)
    assert resolved is not None


def test_generated_keys_are_high_entropy_and_prefixed():
    keys = {generate_public_widget_key() for _ in range(50)}

    assert len(keys) == 50
    for key in keys:
        assert key.startswith(WIDGET_KEY_PREFIX)
        # 24 random bytes rendered as hex.
        assert len(key) == len(WIDGET_KEY_PREFIX) + 48
        int(key[len(WIDGET_KEY_PREFIX):], 16)


# ----------------------------------------------------------------------
# Disable / kill switch
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_is_its_own_action_and_preserves_data(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    result, _ = await _onboard(db, manifest)

    disabled_document = _manifest_document(tenant=_tenant_block(slug))
    disabled_document["public_chat"]["enabled"] = False
    disabled_document["business_integrations"][0]["enabled"] = False
    disabled = parse_manifest(disabled_document)

    plan = await build_plan(db, disabled)
    by_target = {a.target: a for a in plan.actions}
    assert by_target["public_chat"].action == ACTION_DISABLE
    assert by_target[f"business_integration:{PROVIDER_A1}"].action == ACTION_DISABLE

    await apply_plan(db, disabled, plan)

    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    config, rows = await resolve_canonical_public_chat_config(db, organization.id)
    assert rows == 1
    assert config is not None
    assert config.enabled is False
    # Disabling must not destroy history or the key.
    assert config.public_widget_key_hash == hash_digest(result.public_widget_key)
    documents = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.organization_id == organization.id
            )
        )
    ).scalars().all()
    assert len(documents) == 1

    # A disabled widget refuses to resolve.
    from app.services.public_chat_service import PublicChatWidgetDisabledError

    with pytest.raises(PublicChatWidgetDisabledError):
        await public_chat_service.resolve_config(db, result.public_widget_key)


@pytest.mark.asyncio
async def test_disabled_provider_fails_closed_for_that_tenant_only(db):
    kept_slug = _unique_slug()
    killed_slug = _unique_slug()
    kept = parse_manifest(_manifest_document(tenant=_tenant_block(kept_slug)))
    killed = parse_manifest(_manifest_document(tenant=_tenant_block(killed_slug)))

    await _onboard(db, kept)
    await _onboard(db, killed)

    kept_org = (
        await db.execute(select(Organization).where(Organization.external_id == kept_slug))
    ).scalar_one()
    killed_org = (
        await db.execute(select(Organization).where(Organization.external_id == killed_slug))
    ).scalar_one()

    kept_tools = await BusinessIntegrationService.enabled_tool_names(db, kept_org.id)
    killed_tools = await BusinessIntegrationService.enabled_tool_names(
        db, killed_org.id
    )
    assert set(A1_TOOL_NAMES) <= kept_tools
    assert set(A1_TOOL_NAMES) <= killed_tools

    row = await BusinessIntegrationService.get_config(db, killed_org.id, PROVIDER_A1)
    row.enabled = False
    await db.commit()

    # Disabled provider exposes none of its tools for that tenant only.
    assert not set(A1_TOOL_NAMES) & await BusinessIntegrationService.enabled_tool_names(
        db, killed_org.id
    )
    # The other tenant is untouched.
    assert set(A1_TOOL_NAMES) <= await BusinessIntegrationService.enabled_tool_names(
        db, kept_org.id
    )


# ----------------------------------------------------------------------
# Tenant isolation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_tenants_never_share_widgets_or_knowledge(db):
    slug_a = _unique_slug("alpha")
    slug_b = _unique_slug("beta")
    doc_a = _manifest_document(tenant=_tenant_block(slug_a))
    doc_a["public_chat"]["display_name"] = "Alpha Widget"
    doc_a["public_chat"]["allowed_origins"] = ["https://alpha.example"]
    doc_a["knowledge"][0]["source_id"] = "alpha-faq"
    doc_a["knowledge"][0]["content"] = "Alpha answers questions about alpha."

    doc_b = _manifest_document(tenant=_tenant_block(slug_b))
    doc_b["public_chat"]["display_name"] = "Beta Widget"
    doc_b["public_chat"]["allowed_origins"] = ["https://beta.example"]
    doc_b["knowledge"][0]["source_id"] = "beta-faq"
    doc_b["knowledge"][0]["content"] = "Beta answers questions about beta."

    result_a, _ = await _onboard(db, parse_manifest(doc_a))
    result_b, _ = await _onboard(db, parse_manifest(doc_b))

    org_a = (
        await db.execute(select(Organization).where(Organization.external_id == slug_a))
    ).scalar_one()
    org_b = (
        await db.execute(select(Organization).where(Organization.external_id == slug_b))
    ).scalar_one()
    assert org_a.id != org_b.id

    resolved_a = await public_chat_service.resolve_config(db, result_a.public_widget_key)
    resolved_b = await public_chat_service.resolve_config(db, result_b.public_widget_key)
    assert resolved_a.organization_id == org_a.id
    assert resolved_b.organization_id == org_b.id

    # Each tenant's origin allowlist is its own.
    from app.services.public_chat_service import PublicChatOriginNotAllowedError

    with pytest.raises(PublicChatOriginNotAllowedError):
        public_chat_service.validate_embedding_origin(resolved_a, "https://beta.example")
    with pytest.raises(PublicChatOriginNotAllowedError):
        public_chat_service.validate_embedding_origin(resolved_b, "https://alpha.example")

    docs_a = (
        await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.organization_id == org_a.id)
        )
    ).scalars().all()
    docs_b = (
        await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.organization_id == org_b.id)
        )
    ).scalars().all()
    assert [d.metadata_json["onboarding_source_id"] for d in docs_a] == ["alpha-faq"]
    assert [d.metadata_json["onboarding_source_id"] for d in docs_b] == ["beta-faq"]

    chunks = (
        await db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.organization_id == org_b.id)
        )
    ).scalars().all()
    assert chunks
    assert all(chunk.organization_id == org_b.id for chunk in chunks)


@pytest.mark.asyncio
async def test_disabling_one_tenant_leaves_the_other_enabled(db):
    slug_a = _unique_slug("keep")
    slug_b = _unique_slug("stop")
    result_a, _ = await _onboard(
        db, parse_manifest(_manifest_document(tenant=_tenant_block(slug_a)))
    )
    result_b, _ = await _onboard(
        db, parse_manifest(_manifest_document(tenant=_tenant_block(slug_b)))
    )

    doc = _manifest_document(tenant=_tenant_block(slug_b))
    doc["public_chat"]["enabled"] = False
    await apply_plan(db, parse_manifest(doc), await build_plan(db, parse_manifest(doc)))

    assert (await public_chat_service.resolve_config(db, result_a.public_widget_key))
    from app.services.public_chat_service import PublicChatWidgetDisabledError

    with pytest.raises(PublicChatWidgetDisabledError):
        await public_chat_service.resolve_config(db, result_b.public_widget_key)


# ----------------------------------------------------------------------
# Knowledge determinism
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_knowledge_ingestion_is_content_deduplicated(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    result, _ = await _onboard(db, manifest)

    organization_id = result.organization_id
    again = await apply_plan(db, manifest, await build_plan(db, manifest))
    assert again.knowledge_ingested == 0
    assert again.knowledge_duplicates == 1

    count = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.organization_id == organization_id
            )
        )
    ).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_changed_knowledge_version_adds_a_document_without_deleting(db):
    slug = _unique_slug()
    first = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    result, _ = await _onboard(db, first)

    updated = _manifest_document(tenant=_tenant_block(slug))
    updated["knowledge"][0]["version"] = 2
    updated["knowledge"][0]["content"] = (
        "Acme answers general questions during business hours. "
        "Escalation is available weekdays."
    )
    second = parse_manifest(updated)

    plan = await build_plan(db, second)
    action = next(a for a in plan.actions if a.target == "knowledge:acme-faq")
    assert action.action == ACTION_CREATE

    applied = await apply_plan(db, second, plan)
    assert applied.knowledge_ingested == 1

    documents = (
        await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.organization_id == result.organization_id)
            .order_by(KnowledgeDocument.id)
        )
    ).scalars().all()
    # The superseded document is retained, not deleted.
    assert len(documents) == 2
    assert documents[0].metadata_json["onboarding_version"] == 1
    assert documents[1].metadata_json["onboarding_version"] == 2


@pytest.mark.asyncio
async def test_onboarding_does_not_touch_unrelated_tenants_knowledge(db):
    other_slug = _unique_slug("unrelated")
    other = parse_manifest(
        _manifest_document(
            tenant=_tenant_block(other_slug),
            knowledge=[
                {
                    "source_id": "manual-doc",
                    "version": 7,
                    "title": "Hand written",
                    "content": "A document created by hand, not by onboarding.",
                }
            ],
        )
    )
    other_result, _ = await _onboard(db, other)

    target_slug = _unique_slug("target")
    target = parse_manifest(_manifest_document(tenant=_tenant_block(target_slug)))
    await _onboard(db, target)

    untouched = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.organization_id == other_result.organization_id
            )
        )
    ).scalars().all()
    assert len(untouched) == 1
    assert untouched[0].metadata_json["onboarding_version"] == 7
    assert untouched[0].title == "Hand written"


# ----------------------------------------------------------------------
# Ambiguity is reported, not guessed
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_widget_rows_are_reported_as_error(db, monkeypatch):
    """A legacy multi-row tenant is reported, never silently resolved.

    Phase 1P.4 migration ``1p4a0001`` makes this state unrepresentable in a
    migrated database: the unique constraint rejects the second row and the
    migration's own precheck refuses to upgrade a database that already holds
    duplicates. The detector therefore survives only for a database that has
    not been migrated yet, and is simulated here rather than by writing an
    illegal row.
    """
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    # Onboard for the side effect only; this test asserts on the plan built
    # after the duplicate detector is patched in, so neither return value is read.
    _, _ = await _onboard(db, manifest)

    async def _legacy_two_rows(_db, organization_id):
        return (None, 2)

    monkeypatch.setattr(
        planner, "resolve_canonical_public_chat_config", _legacy_two_rows
    )

    plan = await build_plan(db, manifest)
    action = next(a for a in plan.actions if a.target == "public_chat")
    assert action.action == ACTION_ERROR
    assert "2 widget rows" in action.detail
    assert plan.has_errors is True

    with pytest.raises(OnboardingConflictError):
        await apply_plan(db, manifest, plan)


@pytest.mark.asyncio
async def test_organization_name_drift_is_an_update(db):
    slug = _unique_slug()
    first = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    await _onboard(db, first)

    renamed = parse_manifest(
        _manifest_document(tenant=_tenant_block(slug, name=_renamed_name(slug)))
    )
    plan = await build_plan(db, renamed)
    action = next(a for a in plan.actions if a.target == "organization")
    assert action.action == ACTION_UPDATE
    assert action.fields == ("name",)

    await apply_plan(db, renamed, plan)
    organization = (
        await db.execute(select(Organization).where(Organization.external_id == slug))
    ).scalar_one()
    assert organization.name == _renamed_name(slug)


@pytest.mark.asyncio
async def test_business_integration_row_is_created_with_the_tenant(db):
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    result, _ = await _onboard(db, manifest)

    rows = (
        await db.execute(
            select(BusinessIntegrationConfiguration).where(
                BusinessIntegrationConfiguration.organization_id == result.organization_id
            )
        )
    ).scalars().all()
    assert [row.provider for row in rows] == [PROVIDER_A1]
    assert rows[0].enabled is True


@pytest.mark.asyncio
async def test_reapplying_an_unchanged_manifest_is_a_pure_no_op(db):
    """A second apply must report UNCHANGED for every action, including config.

    Regression: the apply path folds ``provider_mode`` into ``config_json`` but
    the diff path compared the raw manifest config against the stored column.
    The stored value therefore always carried one extra key, so every re-run
    reported ``UPDATE ... fields=config`` and rewrote the row. An operator could
    not tell a real config edit from this permanent noise, which defeats the
    purpose of a re-runnable plan.
    """
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))

    first = await build_plan(db, manifest)
    await apply_plan(db, manifest, first)

    second = await build_plan(db, manifest)

    assert second.is_noop, [
        (a.target, a.action, a.fields) for a in second.actions if a.action != ACTION_UNCHANGED
    ]
    assert not second.has_errors
    assert all(a.action == ACTION_UNCHANGED for a in second.actions), [
        (a.target, a.action, a.fields) for a in second.actions
    ]


@pytest.mark.asyncio
async def test_a_real_config_edit_is_still_reported_as_an_update(db):
    """The no-op fix must not blind the planner to genuine drift."""
    slug = _unique_slug()
    manifest = parse_manifest(_manifest_document(tenant=_tenant_block(slug)))
    await apply_plan(db, manifest, await build_plan(db, manifest))

    changed = copy.deepcopy(manifest)
    changed.business_integrations[0].config["demo_region"] = "nz"

    plan = await build_plan(db, changed)
    action = next(a for a in plan.actions if a.target == "business_integration:a1_cash_for_cars")

    assert action.action == ACTION_UPDATE
    assert "config" in action.fields
