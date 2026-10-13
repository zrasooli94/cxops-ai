"""Guided tenant setup (Phase 1Q).

The setup wizard is a view over state that already exists - the
organization row, its single public-chat configuration, its knowledge
documents and its memberships - plus four small writes. Progress is therefore
resumable by construction: there is no separate wizard record to drift out of
step with the real configuration.

Safety properties:

* A widget created here is always ``enabled = false``. Enabling it requires an
  ``active`` organization (operator approval) and goes through the existing
  staff configuration API.
* The widget key is minted once and returned once (same contract as
  onboarding and rotation). A second create is a 409 that never reveals a key;
  the per-tenant unique constraint makes concurrent creates converge on one row.
* Rate/TTL limits and AI allowances are not editable here: they stay with the
  reviewed manifest and platform operators.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.commercial import INVITATION_STATUS_PENDING, OrganizationInvitation
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.organization import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ONBOARDING,
    LIFECYCLE_PENDING_REVIEW,
    Organization,
)
from app.models.organization_membership import OrganizationMembership
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.services.commercial_common import (
    SELF_SERVICE_THEME_TOKENS,
    CommercialValidationError,
    normalize_website,
)
from app.services.tenant_config_service import (
    MAX_DISPLAY_NAME_LENGTH,
    MAX_WELCOME_MESSAGE_LENGTH,
    AmbiguousWidgetError,
    TenantConfigError,
    TenantConfigService,
    TenantWidgetNotFoundError,
    _validate_origins,
)
from app.tenant_onboarding.planner import (
    generate_public_widget_key,
    resolve_canonical_public_chat_config,
)

log = get_logger(__name__)

# Ordered wizard steps. ``required`` steps gate submission for review.
STEPS: tuple[tuple[str, str, bool], ...] = (
    ("business", "Business information", True),
    ("workspace", "Workspace and team", False),
    ("domain", "Website domain", True),
    ("chatbot", "Chatbot configuration", True),
    ("knowledge", "Knowledge setup", False),
    ("install", "Installation", False),
    ("preview", "Preview", False),
    ("review", "Ready for approval", True),
)


class OnboardingError(Exception):
    status_code = 400


class OnboardingConflictError(OnboardingError):
    status_code = 409


class OnboardingNotFoundError(OnboardingError):
    status_code = 404


def _hash(value: str) -> str:
    from app.services.public_chat_service import hash_digest

    return hash_digest(value)


async def _organization(db: AsyncSession, organization_id: int) -> Organization:
    organization = await db.get(Organization, organization_id)
    if organization is None:
        raise OnboardingNotFoundError("Organization not found.")
    return organization


class OnboardingService:
    @staticmethod
    async def state(db: AsyncSession, *, organization_id: int) -> dict[str, Any]:
        organization = await _organization(db, organization_id)
        config, total_rows = await resolve_canonical_public_chat_config(db, organization_id)

        documents = int(
            await db.scalar(
                select(func.count())
                .select_from(KnowledgeDocument)
                .where(KnowledgeDocument.organization_id == organization_id)
            )
            or 0
        )
        chunks = int(
            await db.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.organization_id == organization_id)
            )
            or 0
        )
        members = int(
            await db.scalar(
                select(func.count())
                .select_from(OrganizationMembership)
                .where(OrganizationMembership.organization_id == organization_id)
            )
            or 0
        )
        pending_invitations = int(
            await db.scalar(
                select(func.count())
                .select_from(OrganizationInvitation)
                .where(
                    OrganizationInvitation.organization_id == organization_id,
                    OrganizationInvitation.status == INVITATION_STATUS_PENDING,
                    OrganizationInvitation.expires_at > datetime.now(UTC),
                )
            )
            or 0
        )
        last_session_at = None
        if config is not None:
            last_session_at = await db.scalar(
                select(func.max(PublicChatSession.created_at)).where(
                    PublicChatSession.organization_id == organization_id,
                    PublicChatSession.configuration_id == config.id,
                )
            )

        origins = list(config.allowed_origins or []) if config is not None else []
        status = organization.lifecycle_status
        complete = {
            "business": bool(organization.name and organization.industry and organization.website_url),
            "workspace": members >= 1,
            "domain": bool(origins),
            "chatbot": config is not None
            and bool(config.display_name)
            and bool(config.welcome_message),
            "knowledge": documents > 0,
            "install": last_session_at is not None,
            "preview": config is not None,
            "review": status in {LIFECYCLE_PENDING_REVIEW, LIFECYCLE_ACTIVE},
        }
        steps = []
        for key, title, required in STEPS:
            steps.append(
                {
                    "key": key,
                    "title": title,
                    "required": required,
                    "complete": complete[key],
                }
            )
        missing = [
            step["title"]
            for step in steps
            if step["required"] and step["key"] != "review" and not step["complete"]
        ]
        warnings: list[str] = []
        if total_rows > 1:
            warnings.append(
                "This workspace has more than one widget configuration. Contact support "
                "before continuing."
            )
        if documents == 0:
            warnings.append(
                "No knowledge documents yet. Without them the assistant can only greet "
                "visitors and hand conversations to your team."
            )
        if config is not None and not config.enabled and status == LIFECYCLE_ACTIVE:
            warnings.append("Your workspace is approved but the chatbot is switched off.")
        if status == LIFECYCLE_ONBOARDING:
            warnings.append(
                "The chatbot cannot go live until CXOps approves this workspace. "
                "AI replies and external actions stay disabled until then."
            )

        settings = get_settings()
        knowledge_upload_available = (
            status == LIFECYCLE_ACTIVE or settings.onboarding_knowledge_setup_enabled
        )
        return {
            "organization": {
                "id": organization.id,
                "name": organization.name,
                "industry": organization.industry,
                "website_url": organization.website_url,
                "lifecycle_status": status,
                "plan_code": organization.plan_code,
                "submitted_for_review_at": organization.submitted_for_review_at.isoformat()
                if organization.submitted_for_review_at
                else None,
            },
            "steps": steps,
            "missing_required": missing,
            "can_submit": status == LIFECYCLE_ONBOARDING and not missing and total_rows <= 1,
            "warnings": warnings,
            "widget": None
            if config is None
            else {
                "display_name": config.display_name,
                "welcome_message": config.welcome_message,
                "theme_token": config.theme_token,
                "allowed_origins": origins,
                "enabled": config.enabled,
                "has_widget_key": bool(config.public_widget_key_hash),
                "last_session_at": last_session_at.isoformat() if last_session_at else None,
            },
            "knowledge": {
                "documents": documents,
                "chunks": chunks,
                "upload_available": knowledge_upload_available,
            },
            "team": {"members": members, "pending_invitations": pending_invitations},
            "theme_options": sorted(SELF_SERVICE_THEME_TOKENS),
        }

    @staticmethod
    async def update_business(
        db: AsyncSession,
        *,
        organization_id: int,
        actor_subject: str,
        changes: dict[str, Any],
    ) -> None:
        organization = await _organization(db, organization_id)
        if organization.lifecycle_status == LIFECYCLE_PENDING_REVIEW:
            raise OnboardingConflictError(
                "Business details are locked while the workspace is under review."
            )
        if organization.lifecycle_status not in {LIFECYCLE_ONBOARDING, LIFECYCLE_ACTIVE}:
            raise OnboardingConflictError("This workspace is suspended.")
        changed: list[str] = []
        if "name" in changes and changes["name"] is not None:
            name = str(changes["name"]).strip()
            if not name or len(name) > 255:
                raise OnboardingError("name must be 1-255 characters")
            if name != organization.name:
                organization.name = name
                changed.append("name")
        if "industry" in changes:
            industry = (changes["industry"] or "").strip() or None
            if industry is not None and len(industry) > 120:
                raise OnboardingError("industry must be at most 120 characters")
            if industry != organization.industry:
                organization.industry = industry
                changed.append("industry")
        if "website_url" in changes:
            try:
                website = normalize_website(changes["website_url"])
            except CommercialValidationError as exc:
                raise OnboardingError(str(exc)) from exc
            if website != organization.website_url:
                organization.website_url = website
                changed.append("website_url")
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise OnboardingConflictError(
                "Another workspace already uses this business name."
            ) from exc
        log.info(
            "onboarding_business_updated",
            organization_id=organization_id,
            actor_subject=actor_subject,
            changed_fields=sorted(changed),
        )

    @staticmethod
    async def create_widget(
        db: AsyncSession,
        *,
        organization_id: int,
        actor_subject: str,
        display_name: str,
        welcome_message: str,
        theme_token: str | None,
        allowed_origins: list[str],
    ) -> dict[str, Any]:
        organization = await _organization(db, organization_id)
        if organization.lifecycle_status == "suspended":
            raise OnboardingConflictError("This workspace is suspended.")
        existing, _total = await resolve_canonical_public_chat_config(db, organization_id)
        if existing is not None:
            raise OnboardingConflictError(
                "This workspace already has a chatbot. Its key cannot be shown again; "
                "an administrator can rotate it from the chatbot settings."
            )
        display = display_name.strip()
        welcome = welcome_message.strip()
        if not display or len(display) > MAX_DISPLAY_NAME_LENGTH:
            raise OnboardingError(f"display name must be 1-{MAX_DISPLAY_NAME_LENGTH} characters")
        if not welcome or len(welcome) > MAX_WELCOME_MESSAGE_LENGTH:
            raise OnboardingError(
                f"welcome message must be 1-{MAX_WELCOME_MESSAGE_LENGTH} characters"
            )
        if theme_token is not None and theme_token not in SELF_SERVICE_THEME_TOKENS:
            raise OnboardingError("unsupported theme")
        try:
            origins = _validate_origins(list(allowed_origins))
        except TenantConfigError as exc:
            raise OnboardingError(str(exc)) from exc

        widget_key = generate_public_widget_key()
        config = PublicChatConfiguration(
            organization_id=organization_id,
            public_widget_key_hash=_hash(widget_key),
            display_name=display,
            welcome_message=welcome,
            allowed_origins=origins,
            theme_token=theme_token,
            # Never live on creation: going live needs operator approval and
            # an explicit switch by the tenant.
            enabled=False,
        )
        db.add(config)
        try:
            await db.commit()
        except IntegrityError as exc:
            # A concurrent create for the same tenant won (one widget per
            # tenant is a unique constraint). The loser never sees a key.
            await db.rollback()
            raise OnboardingConflictError(
                "This workspace already has a chatbot."
            ) from exc
        log.info(
            "onboarding_widget_created",
            organization_id=organization_id,
            actor_subject=actor_subject,
            configuration_id=config.id,
            origin_count=len(origins),
        )
        return {
            "public_widget_key": widget_key,
            "warning": (
                "This key is shown once and cannot be retrieved later. Copy the "
                "installation snippet now; if you lose it, rotate the key."
            ),
        }

    @staticmethod
    async def update_widget(
        db: AsyncSession,
        *,
        organization_id: int,
        actor_subject: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        """Branding and origins during setup. ``enabled`` is not accepted here."""
        if "enabled" in changes:
            raise OnboardingError("The chatbot is switched on from its settings after approval.")
        # What the operator reviews (domains, public text) must not change
        # while the review is open.
        status = (await _organization(db, organization_id)).lifecycle_status
        if status == LIFECYCLE_PENDING_REVIEW:
            raise OnboardingConflictError(
                "Chatbot settings are locked while the workspace is under review."
            )
        if status not in {LIFECYCLE_ONBOARDING, LIFECYCLE_ACTIVE}:
            raise OnboardingConflictError("This workspace is suspended.")
        theme = changes.get("theme_token")
        if theme is not None and theme not in SELF_SERVICE_THEME_TOKENS:
            raise OnboardingError("unsupported theme")
        try:
            return await TenantConfigService.update_public_chat(
                db,
                organization_id=organization_id,
                changes=changes,
                actor_subject=actor_subject,
            )
        except (TenantWidgetNotFoundError, AmbiguousWidgetError) as exc:
            raise OnboardingConflictError(str(exc)) from exc
        except TenantConfigError as exc:
            raise OnboardingError(str(exc)) from exc

    @classmethod
    async def submit_for_review(
        cls, db: AsyncSession, *, organization_id: int, actor_subject: str
    ) -> dict[str, Any]:
        state = await cls.state(db, organization_id=organization_id)
        status = state["organization"]["lifecycle_status"]
        if status == LIFECYCLE_PENDING_REVIEW:
            return state
        if status != LIFECYCLE_ONBOARDING:
            raise OnboardingConflictError(f"A {status} workspace cannot be submitted for review.")
        if not state["can_submit"]:
            raise OnboardingConflictError(
                "Finish the required steps first: " + ", ".join(state["missing_required"])
            )
        organization = (
            await db.execute(
                select(Organization).where(Organization.id == organization_id).with_for_update()
            )
        ).scalar_one()
        if organization.lifecycle_status == LIFECYCLE_ONBOARDING:
            now = datetime.now(UTC)
            organization.lifecycle_status = LIFECYCLE_PENDING_REVIEW
            organization.submitted_for_review_at = now
            organization.lifecycle_changed_at = now
            organization.lifecycle_changed_by = actor_subject
        await db.commit()
        log.info(
            "onboarding_submitted_for_review",
            organization_id=organization_id,
            actor_subject=actor_subject,
        )
        return await cls.state(db, organization_id=organization_id)
