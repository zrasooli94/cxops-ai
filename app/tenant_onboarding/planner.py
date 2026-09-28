"""Plan / apply engine for tenant onboarding (Phase 1P.3).

Planning is read-only and re-runnable. Applying re-validates every precondition
inside its own transaction, so a plan built minutes ago against a changed
database fails loudly instead of writing something the operator never reviewed.

Legacy databases are still handled explicitly rather than papered over. Until
Phase 1P.4, ``public_chat_configurations.organization_id`` carried a *non-unique*
index, so a tenant could end up with several widget rows; migration ``1p4a0001``
now enforces one widget row per tenant with a unique constraint and refuses to
upgrade a database that already has duplicates. A manifest declares exactly one
widget per tenant, so the multi-row detector below is retained for any database
that has not yet been migrated: such a tenant is reported as ``ERROR`` instead of
the tool silently picking one and leaving the others enabled.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_integration import BusinessIntegrationConfiguration
from app.models.organization import Organization
from app.models.public_chat import PublicChatConfiguration
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.public_chat_service import hash_digest
from app.tenant_onboarding.manifest import (
    BusinessIntegrationManifest,
    PublicChatManifest,
    TenantManifest,
)

# Kept identical to scripts/create_public_chat_config.py so a key minted by
# either path is indistinguishable to the runtime and to the hash lookup.
WIDGET_KEY_PREFIX = "pk_live_"
WIDGET_KEY_ENTROPY_BYTES = 24
MAX_KEY_GENERATION_ATTEMPTS = 5

ACTION_CREATE = "CREATE"
ACTION_UPDATE = "UPDATE"
ACTION_UNCHANGED = "UNCHANGED"
ACTION_DISABLE = "DISABLE"
ACTION_ERROR = "ERROR"

WRITE_ACTIONS = frozenset({ACTION_CREATE, ACTION_UPDATE, ACTION_DISABLE})

# Path a customer embeds on the customer-facing site. The widget key travels in
# the query string; the embedding origin is validated server-side against the
# tenant's allowlist, and the key itself is public by design.
EMBED_PATH = "/chat/embed"


class OnboardingConflictError(RuntimeError):
    """The database no longer matches the plan that was reviewed."""


def generate_public_widget_key() -> str:
    """Mint a high-entropy public widget key.

    The key is treated as public (it ships in customer-facing HTML); only its
    SHA-256 digest is persisted. The prefix is cosmetic so a leaked key is
    obvious in a page source.
    """
    return f"{WIDGET_KEY_PREFIX}{secrets.token_hex(WIDGET_KEY_ENTROPY_BYTES)}"


def build_embed_snippet(
    frontend_base_url: str,
    widget_key: str,
    display_name: str,
) -> str:
    """Return the copy-paste embed snippet for a customer's website."""
    base = frontend_base_url.rstrip("/")
    src = f"{base}{EMBED_PATH}?key={quote(widget_key, safe='')}"
    title = display_name.replace('"', "&quot;")
    return (
        f'<iframe\n'
        f'  src="{src}"\n'
        f'  title="Chat with {title}"\n'
        f'  width="400"\n'
        f'  height="620"\n'
        f'  loading="lazy"\n'
        f'  style="border:0;max-width:100%;"\n'
        f"  allowtransparency=\"true\"\n"
        f"></iframe>"
    )


@dataclass(frozen=True)
class OnboardingAction:
    """One reviewable unit of work."""

    target: str
    action: str
    detail: str = ""
    fields: tuple[str, ...] = ()

    @property
    def is_write(self) -> bool:
        return self.action in WRITE_ACTIONS

    @property
    def is_error(self) -> bool:
        return self.action == ACTION_ERROR


@dataclass
class OnboardingPlan:
    """A reviewable description of what onboarding would do."""

    slug: str
    manifest_path: str | None
    actions: list[OnboardingAction] = field(default_factory=list)
    organization_exists: bool = False
    organization_id: int | None = None
    mints_widget_key: bool = False
    rotates_widget_key: bool = False

    def add(
        self,
        target: str,
        action: str,
        detail: str = "",
        fields: tuple[str, ...] = (),
    ) -> None:
        self.actions.append(
            OnboardingAction(target=target, action=action, detail=detail, fields=fields)
        )

    @property
    def has_errors(self) -> bool:
        return any(action.is_error for action in self.actions)

    @property
    def write_actions(self) -> list[OnboardingAction]:
        return [action for action in self.actions if action.is_write]

    @property
    def is_noop(self) -> bool:
        return not self.has_errors and not self.write_actions

    def summary_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {
            ACTION_CREATE: 0,
            ACTION_UPDATE: 0,
            ACTION_UNCHANGED: 0,
            ACTION_DISABLE: 0,
            ACTION_ERROR: 0,
        }
        for action in self.actions:
            counts[action.action] = counts.get(action.action, 0) + 1
        return counts

    def describe(self) -> list[str]:
        lines: list[str] = []
        for action in self.actions:
            suffix = f" ({action.detail})" if action.detail else ""
            if action.fields:
                suffix = f"{suffix} fields={','.join(action.fields)}" if suffix else (
                    f" fields={','.join(action.fields)}"
                )
            lines.append(f"  {action.action:<9} {action.target}{suffix}")
        return lines


@dataclass
class OnboardingResult:
    """Outcome of an apply, including the one-time key material."""

    plan: OnboardingPlan
    organization_id: int
    public_widget_key: str | None = None
    widget_key_created: bool = False
    widget_key_rotated: bool = False
    knowledge_ingested: int = 0
    knowledge_duplicates: int = 0


async def resolve_canonical_public_chat_config(
    db: AsyncSession,
    organization_id: int,
) -> tuple[PublicChatConfiguration | None, int]:
    """Return ``(config, total_rows)`` for a tenant's widget records.

    ``total_rows`` is reported so the planner can distinguish "no widget" from
    "ambiguous widget", which the schema permits but the manifest does not.
    """
    result = await db.execute(
        select(PublicChatConfiguration)
        .where(PublicChatConfiguration.organization_id == organization_id)
        .order_by(PublicChatConfiguration.id)
    )
    configs = list(result.scalars().all())
    if not configs:
        return None, 0
    if len(configs) > 1:
        return None, len(configs)
    return configs[0], 1


def _public_chat_diff(
    existing: PublicChatConfiguration,
    desired: PublicChatManifest,
) -> tuple[str, tuple[str, ...]]:
    """Return ``(action, changed_fields)`` for one widget row.

    Disabling is its own action so an operator can tell a deliberate kill switch
    apart from an ordinary settings update in the plan output.
    """
    changed: list[str] = []
    if existing.display_name != desired.display_name:
        changed.append("display_name")
    if existing.welcome_message != desired.welcome_message:
        changed.append("welcome_message")
    if list(existing.allowed_origins or []) != list(desired.allowed_origins):
        changed.append("allowed_origins")
    if existing.theme_token != desired.theme_token:
        changed.append("theme_token")
    if existing.max_message_length != desired.max_message_length:
        changed.append("max_message_length")
    if existing.max_messages_per_minute != desired.max_messages_per_minute:
        changed.append("max_messages_per_minute")
    if existing.session_ttl_hours != desired.session_ttl_hours:
        changed.append("session_ttl_hours")

    if not desired.enabled and existing.enabled:
        return ACTION_DISABLE, tuple(changed)
    if existing.enabled != desired.enabled:
        changed.append("enabled")
    if not changed:
        return ACTION_UNCHANGED, ()
    return ACTION_UPDATE, tuple(changed)


def _integration_diff(
    existing: BusinessIntegrationConfiguration | None,
    desired: BusinessIntegrationManifest,
) -> tuple[str, tuple[str, ...]]:
    if existing is None:
        return ACTION_CREATE, ()
    changed: list[str] = []
    if existing.enabled != desired.enabled:
        changed.append("enabled")
    desired_config: dict[str, Any] = dict(desired.config)
    if desired.provider_mode:
        existing_mode = (existing.config_json or {}).get("provider_mode")
        if existing_mode != desired.provider_mode:
            changed.append("provider_mode")
    # Compare the same shape the apply path writes. ``_apply_business_integration``
    # folds provider_mode into config_json, so diffing the raw manifest config
    # against the stored column would always differ by that one key and report a
    # config UPDATE on every run -- making a no-op re-onboard look like a change
    # and hiding real edits behind permanent noise.
    if desired.provider_mode:
        desired_config["provider_mode"] = desired.provider_mode
    if desired_config and (existing.config_json or {}) != desired_config:
        changed.append("config")
    if not changed:
        return ACTION_UNCHANGED, ()
    if not desired.enabled and existing.enabled:
        return ACTION_DISABLE, tuple(changed)
    return ACTION_UPDATE, tuple(changed)


async def _knowledge_exists(
    db: AsyncSession,
    organization_id: int,
    content: str,
) -> tuple[bool, str]:
    checksum = KnowledgeIngestionService.checksum(
        KnowledgeIngestionService.clean_text(content)
    )
    existing = await KnowledgeRepository.get_document_by_checksum_for_tenant(
        db=db,
        checksum=checksum,
        organization_id=organization_id,
    )
    return existing is not None, checksum


async def build_plan(
    db: AsyncSession,
    manifest: TenantManifest,
    *,
    rotate_widget_key: bool = False,
) -> OnboardingPlan:
    """Compute the onboarding plan without writing anything.

    A caller running ``--dry-run`` stops here. Knowledge is checked by content
    checksum, which is the same duplicate guard ingestion uses, so the plan
    predicts duplication exactly.
    """
    plan = OnboardingPlan(
        slug=manifest.slug,
        manifest_path=manifest.origin_path,
    )

    org_result = await db.execute(
        select(Organization).where(Organization.external_id == manifest.slug)
    )
    organization = org_result.scalar_one_or_none()

    if organization is None:
        plan.add(
            "organization",
            ACTION_CREATE,
            f"create tenant {manifest.slug!r} ({manifest.name!r})",
        )
    else:
        plan.organization_exists = True
        plan.organization_id = organization.id
        changed: list[str] = []
        if organization.name != manifest.name:
            changed.append("name")
        if organization.industry != manifest.industry:
            changed.append("industry")
        if changed:
            plan.add("organization", ACTION_UPDATE, f"tenant {manifest.slug!r}", tuple(changed))
        else:
            plan.add("organization", ACTION_UNCHANGED, f"tenant {manifest.slug!r}")

    if manifest.public_chat is not None:
        await _plan_public_chat(db, plan, manifest, organization, rotate_widget_key)

    for integration in manifest.business_integrations:
        await _plan_integration(db, plan, manifest, organization, integration)

    if manifest.knowledge and organization is not None:
        for document in manifest.knowledge:
            target = f"knowledge:{document.source_id}"
            already_present, _checksum = await _knowledge_exists(
                db,
                organization.id,
                document.content,
            )
            if already_present:
                plan.add(
                    target,
                    ACTION_UNCHANGED,
                    f"content already present (version {document.version})",
                )
            else:
                plan.add(
                    target,
                    ACTION_CREATE,
                    f"ingest version {document.version}",
                )
    elif manifest.knowledge:
        for document in manifest.knowledge:
            plan.add(
                f"knowledge:{document.source_id}",
                ACTION_CREATE,
                "tenant is created in this run; document ingests after the tenant exists",
            )

    if manifest.pilot is not None:
        plan.add(
            "pilot",
            ACTION_UNCHANGED,
            f"state={manifest.pilot.state} (documentation only, no runtime effect)",
        )

    return plan


async def _plan_public_chat(
    db: AsyncSession,
    plan: OnboardingPlan,
    manifest: TenantManifest,
    organization: Organization | None,
    rotate_widget_key: bool,
) -> None:
    desired = manifest.public_chat
    assert desired is not None

    if organization is None:
        plan.add(
            "public_chat",
            ACTION_CREATE,
            "widget row is created with the tenant; a widget key is minted on apply",
        )
        plan.mints_widget_key = True
        return

    config, total_rows = await resolve_canonical_public_chat_config(db, organization.id)
    if total_rows > 1:
        plan.add(
            "public_chat",
            ACTION_ERROR,
            (
                f"tenant has {total_rows} widget rows; a manifest declares exactly one. "
                "Disable or delete the extras by hand before onboarding."
            ),
        )
        return
    if config is None:
        plan.add("public_chat", ACTION_CREATE, "no widget row for this tenant")
        plan.mints_widget_key = True
        return

    action, changed = _public_chat_diff(config, desired)
    if rotate_widget_key:
        if action == ACTION_UNCHANGED:
            action = ACTION_UPDATE
        plan.mints_widget_key = True
        plan.rotates_widget_key = True
        detail = "explicit key rotation requested; the previous key stops working immediately"
        plan.add("public_chat", action, detail, changed or ("public_widget_key",))
        return

    if action == ACTION_UNCHANGED:
        detail = "existing key is preserved"
    elif action == ACTION_DISABLE:
        detail = "widget disabled; new sessions and messages are rejected"
    else:
        detail = "existing key is preserved"
    plan.add("public_chat", action, detail, changed)


async def _plan_integration(
    db: AsyncSession,
    plan: OnboardingPlan,
    manifest: TenantManifest,
    organization: Organization | None,
    integration: BusinessIntegrationManifest,
) -> None:
    target = f"business_integration:{integration.provider}"
    if organization is None:
        plan.add(
            target,
            ACTION_CREATE,
            f"enabled={integration.enabled} (row created with the tenant)",
        )
        return

    existing = await db.execute(
        select(BusinessIntegrationConfiguration).where(
            BusinessIntegrationConfiguration.organization_id == organization.id,
            BusinessIntegrationConfiguration.provider == integration.provider,
        )
    )
    action, changed = _integration_diff(existing.scalar_one_or_none(), integration)
    detail = (
        "provider disabled; its tools fail closed for this tenant while history is kept"
        if action == ACTION_DISABLE
        else f"mode={integration.provider_mode}"
    )
    plan.add(target, action, detail, changed)


async def _mint_unique_widget_key(db: AsyncSession) -> str:
    """Mint a key whose digest does not already exist.

    The key-hash column is unique, so a (astronomically unlikely) collision
    would surface as an IntegrityError at flush time. Retrying here turns that
    into a silent retry instead of a failed onboarding.
    """
    for _attempt in range(MAX_KEY_GENERATION_ATTEMPTS):
        candidate = generate_public_widget_key()
        digest = hash_digest(candidate)
        existing = await db.execute(
            select(PublicChatConfiguration.id).where(
                PublicChatConfiguration.public_widget_key_hash == digest
            )
        )
        if existing.scalar_one_or_none() is None:
            return candidate
    raise OnboardingConflictError(
        "could not mint a unique public widget key after "
        f"{MAX_KEY_GENERATION_ATTEMPTS} attempts"
    )


async def apply_plan(
    db: AsyncSession,
    manifest: TenantManifest,
    plan: OnboardingPlan,
    *,
    rotate_widget_key: bool = False,
) -> OnboardingResult:
    """Apply a reviewed plan.

    The tenant, widget row, and business-integration rows are written in one
    transaction. Knowledge documents are ingested after that commit because
    ``KnowledgeIngestionService.ingest`` owns its own commit; re-running is
    safe because ingestion is checksum-deduplicated, so a partially applied
    knowledge set converges on the next run.
    """
    if plan.has_errors:
        raise OnboardingConflictError(
            "plan contains ERROR actions; resolve them before applying: "
            + "; ".join(
                f"{action.target}: {action.detail}"
                for action in plan.actions
                if action.is_error
            )
        )

    # Re-check inside the write path: the plan may have been built earlier.
    org_result = await db.execute(
        select(Organization).where(Organization.external_id == manifest.slug)
    )
    organization = org_result.scalar_one_or_none()
    if organization is None:
        organization = Organization(
            external_id=manifest.slug,
            name=manifest.name,
            industry=manifest.industry,
        )
        db.add(organization)
        await db.flush()
    else:
        organization.name = manifest.name
        organization.industry = manifest.industry
        await db.flush()

    public_widget_key: str | None = None
    widget_key_created = False
    widget_key_rotated = False

    if manifest.public_chat is not None:
        public_widget_key, widget_key_created, widget_key_rotated = await _apply_public_chat(
            db,
            organization.id,
            manifest.public_chat,
            rotate_widget_key=rotate_widget_key,
        )

    for integration in manifest.business_integrations:
        await _apply_integration(db, organization.id, integration)

    await db.commit()

    knowledge_ingested = 0
    knowledge_duplicates = 0
    if manifest.knowledge:
        for document in manifest.knowledge:
            result = await KnowledgeIngestionService.ingest(
                db=db,
                organization_id=organization.id,
                title=document.title,
                content=document.content,
                source=document.source(),
                source_uri=document.source_uri,
                metadata=document.metadata(),
            )
            if result.get("duplicate"):
                knowledge_duplicates += 1
            else:
                knowledge_ingested += 1

    return OnboardingResult(
        plan=plan,
        organization_id=organization.id,
        public_widget_key=public_widget_key,
        widget_key_created=widget_key_created,
        widget_key_rotated=widget_key_rotated,
        knowledge_ingested=knowledge_ingested,
        knowledge_duplicates=knowledge_duplicates,
    )


async def _apply_public_chat(
    db: AsyncSession,
    organization_id: int,
    desired: PublicChatManifest,
    *,
    rotate_widget_key: bool,
) -> tuple[str | None, bool, bool]:
    config, total_rows = await resolve_canonical_public_chat_config(db, organization_id)
    if total_rows > 1:
        raise OnboardingConflictError(
            f"tenant {organization_id} has {total_rows} widget rows; refusing to guess"
        )

    if config is None:
        minted = await _mint_unique_widget_key(db)
        config = PublicChatConfiguration(
            organization_id=organization_id,
            public_widget_key_hash=hash_digest(minted),
            display_name=desired.display_name,
            welcome_message=desired.welcome_message,
            allowed_origins=list(desired.allowed_origins),
            enabled=desired.enabled,
            theme_token=desired.theme_token,
            max_message_length=desired.max_message_length,
            max_messages_per_minute=desired.max_messages_per_minute,
            session_ttl_hours=desired.session_ttl_hours,
        )
        db.add(config)
        await db.flush()
        return minted, True, False

    created = False
    rotated = False
    # None means "keep the existing key", which is the default for a re-apply.
    widget_key: str | None = None
    if rotate_widget_key:
        widget_key = await _mint_unique_widget_key(db)
        config.public_widget_key_hash = hash_digest(widget_key)
        rotated = True

    config.display_name = desired.display_name
    config.welcome_message = desired.welcome_message
    config.allowed_origins = list(desired.allowed_origins)
    config.enabled = desired.enabled
    config.theme_token = desired.theme_token
    config.max_message_length = desired.max_message_length
    config.max_messages_per_minute = desired.max_messages_per_minute
    config.session_ttl_hours = desired.session_ttl_hours
    await db.flush()
    return widget_key, created, rotated


async def _apply_integration(
    db: AsyncSession,
    organization_id: int,
    desired: BusinessIntegrationManifest,
) -> None:
    config = await db.execute(
        select(BusinessIntegrationConfiguration).where(
            BusinessIntegrationConfiguration.organization_id == organization_id,
            BusinessIntegrationConfiguration.provider == desired.provider,
        )
    )
    row = config.scalar_one_or_none()

    config_json: dict[str, Any] = dict(desired.config)
    if desired.provider_mode:
        config_json["provider_mode"] = desired.provider_mode

    if row is None:
        db.add(
            BusinessIntegrationConfiguration(
                organization_id=organization_id,
                provider=desired.provider,
                enabled=desired.enabled,
                config_json=config_json,
            )
        )
    else:
        row.enabled = desired.enabled
        if config_json:
            row.config_json = {**(row.config_json or {}), **config_json}
    await db.flush()


async def list_tenant_widget_keys_status(
    db: AsyncSession,
    organization_id: int,
) -> dict[str, Any]:
    """Summarise widget state for operators without exposing any secret.

    Only the presence of a key digest is reported, never the digest itself and
    never the raw key.
    """
    config, total_rows = await resolve_canonical_public_chat_config(db, organization_id)
    if total_rows > 1:
        return {"widget_rows": total_rows, "ambiguous": True}
    if config is None:
        return {"widget_rows": 0, "ambiguous": False, "has_key": False, "enabled": False}
    return {
        "widget_rows": 1,
        "ambiguous": False,
        "has_key": bool(config.public_widget_key_hash),
        "enabled": config.enabled,
        "origin_count": len(config.allowed_origins or []),
    }


__all__ = [
    "ACTION_CREATE",
    "ACTION_DISABLE",
    "ACTION_ERROR",
    "ACTION_UNCHANGED",
    "ACTION_UPDATE",
    "EMBED_PATH",
    "WIDGET_KEY_PREFIX",
    "OnboardingAction",
    "OnboardingConflictError",
    "OnboardingPlan",
    "OnboardingResult",
    "apply_plan",
    "build_embed_snippet",
    "build_plan",
    "generate_public_widget_key",
    "list_tenant_widget_keys_status",
    "resolve_canonical_public_chat_config",
]
