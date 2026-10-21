"""Staff tenant configuration API (Phase 1P.3).

Gated on ``integration.manage`` and scoped to the caller's resolved tenant, so
a staff member reads and writes only their own tenant's widget and provider
state. No endpoint accepts an organization id: the tenant comes from the
principal's membership context, exactly as in the Phase 1P.2 handoff routes.

Widget-key rotation is its own endpoint rather than a field on the update body,
because it is the one irreversible action here: the old key stops working
immediately and the new key is displayed once.
"""

import hashlib
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.schemas.tenant_config import (
    BusinessIntegrationsResponse,
    BusinessIntegrationState,
    BusinessIntegrationStateResponse,
    BusinessIntegrationUpdateRequest,
    OfflineFaqItem,
    OfflineFaqsResponse,
    OfflineFaqsUpdateRequest,
    PublicChatSettingsResponse,
    PublicChatUpdateRequest,
    WidgetKeyRotationResponse,
)
from app.services.tenant_config_service import (
    CLEARABLE_PUBLIC_CHAT_FIELDS,
    AmbiguousWidgetError,
    TenantConfigError,
    TenantConfigService,
    TenantWidgetNotFoundError,
    UnsupportedProviderError,
)

router = APIRouter(
    prefix="/staff/tenant-config",
    tags=["Staff Tenant Config"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

ManageAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.INTEGRATION_MANAGE)),
]


def _require_local_offline_demo() -> None:
    settings = get_settings()
    host = urlsplit(
        settings.database_url.replace("postgresql+asyncpg", "postgresql")
    ).hostname
    if (
        settings.environment == "production"
        or not settings.public_chat_offline_provider_enabled
        or host not in {"localhost", "127.0.0.1", None}
    ):
        raise HTTPException(status_code=404, detail="Not found")


def _fail(exc: Exception) -> None:
    """Map service errors onto HTTP without leaking internals."""
    if isinstance(exc, TenantWidgetNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, AmbiguousWidgetError):
        # 409, not 400: the data is inconsistent, not the request.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, UnsupportedProviderError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, TenantConfigError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    raise exc


@router.get(
    "/public-chat",
    response_model=PublicChatSettingsResponse,
)
async def get_public_chat_settings(
    db: DatabaseSession,
    tenant: CurrentTenant,
    _authz: ManageAuthz,
):
    """Read this tenant's widget configuration.

    The stored key digest is never returned. When a key exists, staff see
    "key unavailable; rotate to generate a new one".
    """
    try:
        payload = await TenantConfigService.get_public_chat(
            db,
            organization_id=tenant.organization_id,
        )
    except (TenantWidgetNotFoundError, AmbiguousWidgetError) as exc:
        _fail(exc)
        raise  # pragma: no cover - _fail always raises
    return PublicChatSettingsResponse(**payload)


@router.patch(
    "/public-chat",
    response_model=PublicChatSettingsResponse,
)
async def update_public_chat_settings(
    payload: PublicChatUpdateRequest,
    db: DatabaseSession,
    tenant: CurrentTenant,
    principal: CurrentPrincipal,
    _authz: ManageAuthz,
):
    """Update this tenant's widget configuration.

    Disabling the widget (or emptying the origin allowlist) stops new sessions
    and messages immediately while existing conversations, tickets, and
    knowledge are left untouched. That is the tenant-level kill switch.
    """
    # `model_fields_set` is the only way to tell an absent field from an
    # explicit null, because every field is declared `X | None` with a default.
    # Dropping nulls outright would make "remove the theme token" identical to
    # "leave the theme token alone", so the operator could never clear it.
    changes: dict[str, Any] = {}
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if value is None and field not in CLEARABLE_PUBLIC_CHAT_FIELDS:
            # A null for a NOT NULL column is a client mistake. Ignore it so the
            # caller gets its unchanged config rather than a confusing error.
            continue
        changes[field] = value

    try:
        result = await TenantConfigService.update_public_chat(
            db,
            organization_id=tenant.organization_id,
            changes=changes,
            actor_subject=principal.subject,
        )
    except (
        TenantWidgetNotFoundError,
        AmbiguousWidgetError,
        TenantConfigError,
    ) as exc:
        _fail(exc)
        raise  # pragma: no cover - _fail always raises
    return PublicChatSettingsResponse(**result)


@router.get("/offline-faqs", response_model=OfflineFaqsResponse)
async def get_offline_faqs(
    db: DatabaseSession,
    tenant: CurrentTenant,
    _authz: ManageAuthz,
):
    """Read synthetic FAQs only in the disposable local offline demo."""
    _require_local_offline_demo()
    rows = (
        await db.execute(
            select(KnowledgeDocument.title, KnowledgeChunk.content)
            .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(
                KnowledgeDocument.organization_id == tenant.organization_id,
                KnowledgeDocument.source == "local_synthetic",
            )
            .order_by(KnowledgeChunk.chunk_index, KnowledgeDocument.id)
        )
    ).all()
    return OfflineFaqsResponse(
        items=[OfflineFaqItem(title=title, content=content) for title, content in rows]
    )


@router.put("/offline-faqs", response_model=OfflineFaqsResponse)
async def replace_offline_faqs(
    payload: OfflineFaqsUpdateRequest,
    db: DatabaseSession,
    tenant: CurrentTenant,
    _principal: CurrentPrincipal,
    _authz: ManageAuthz,
):
    """Replace only this tenant's local synthetic FAQ pack."""
    _require_local_offline_demo()
    document_ids = select(KnowledgeDocument.id).where(
        KnowledgeDocument.organization_id == tenant.organization_id,
        KnowledgeDocument.source == "local_synthetic",
    )
    await db.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.organization_id == tenant.organization_id,
            KnowledgeChunk.document_id.in_(document_ids),
        )
    )
    await db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(document_ids)))
    for index, item in enumerate(payload.items):
        document = KnowledgeDocument(
            organization_id=tenant.organization_id,
            title=item.title.strip(),
            source="local_synthetic",
            checksum=hashlib.sha256(item.content.strip().encode()).hexdigest(),
            metadata_json={"demo": True, "synthetic": True, "owner_edited": True},
        )
        db.add(document)
        await db.flush()
        db.add(
            KnowledgeChunk(
                organization_id=tenant.organization_id,
                document_id=document.id,
                chunk_index=index,
                content=item.content.strip(),
                token_count=len(item.content.split()),
                metadata_json={"demo": True, "synthetic": True, "owner_edited": True},
                embedding=[0.0] * 1536,
            )
        )
    await db.commit()
    return OfflineFaqsResponse(items=payload.items)


@router.post(
    "/public-chat/rotate-widget-key",
    response_model=WidgetKeyRotationResponse,
)
async def rotate_public_widget_key(
    db: DatabaseSession,
    tenant: CurrentTenant,
    principal: CurrentPrincipal,
    _authz: ManageAuthz,
):
    """Mint a new public widget key. The previous key stops working at once."""
    try:
        return await TenantConfigService.rotate_widget_key(
            db,
            organization_id=tenant.organization_id,
            actor_subject=principal.subject,
        )
    except (
        TenantWidgetNotFoundError,
        AmbiguousWidgetError,
        TenantConfigError,
    ) as exc:
        _fail(exc)
        raise  # pragma: no cover - _fail always raises


@router.get(
    "/business-integrations",
    response_model=BusinessIntegrationsResponse,
)
async def list_business_integrations(
    db: DatabaseSession,
    tenant: CurrentTenant,
    _authz: ManageAuthz,
):
    """Known providers and whether they are enabled for this tenant."""
    payload = await TenantConfigService.list_business_integrations(
        db,
        organization_id=tenant.organization_id,
    )
    return BusinessIntegrationsResponse(
        integrations=[BusinessIntegrationState(**item) for item in payload["integrations"]]
    )


@router.post(
    "/business-integrations/{provider}",
    response_model=BusinessIntegrationStateResponse,
)
async def set_business_integration(
    provider: str,
    payload: BusinessIntegrationUpdateRequest,
    db: DatabaseSession,
    tenant: CurrentTenant,
    principal: CurrentPrincipal,
    _authz: ManageAuthz,
):
    """Enable or disable a provider for this tenant.

    Disabling a provider stops its tools being offered or executed for this
    tenant (they fail closed) while historical business-action rows are
    preserved for audit.
    """
    try:
        return await TenantConfigService.set_business_integration(
            db,
            organization_id=tenant.organization_id,
            provider=provider,
            enabled=payload.enabled,
            actor_subject=principal.subject,
        )
    except UnsupportedProviderError as exc:
        _fail(exc)
        raise  # pragma: no cover - _fail always raises
