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

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import delete, func, select
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
    OfflineDocumentsResponse,
    OfflineDocumentSummary,
    OfflineDocumentUploadResponse,
    OfflineFaqItem,
    OfflineFaqsResponse,
    OfflineFaqsUpdateRequest,
    PublicChatSettingsResponse,
    PublicChatUpdateRequest,
    WidgetKeyRotationResponse,
)
from app.services.document_parser_service import (
    DocumentParserService,
    UnsupportedDocumentTypeError,
)
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
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

MAX_LOCAL_DOCUMENT_BYTES = 512 * 1024
MAX_LOCAL_DOCUMENTS = 20
LOCAL_DOCUMENT_SOURCE = "local_synthetic_document"


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


@router.get("/offline-documents", response_model=OfflineDocumentsResponse)
async def list_offline_documents(
    db: DatabaseSession,
    tenant: CurrentTenant,
    _authz: ManageAuthz,
):
    """List locally uploaded synthetic documents without exposing contents."""
    _require_local_offline_demo()
    rows = (
        await db.execute(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.organization_id == tenant.organization_id,
                KnowledgeDocument.source == LOCAL_DOCUMENT_SOURCE,
            )
            .order_by(KnowledgeDocument.id.desc())
            .limit(MAX_LOCAL_DOCUMENTS)
        )
    ).scalars().all()
    items = []
    for document in rows:
        chunk_count = int(
            await db.scalar(
                select(func.count(KnowledgeChunk.id)).where(
                    KnowledgeChunk.organization_id == tenant.organization_id,
                    KnowledgeChunk.document_id == document.id,
                )
            )
            or 0
        )
        items.append(
            OfflineDocumentSummary(
                document_id=document.id,
                title=document.title,
                filename=(document.metadata_json or {}).get("filename", "document"),
                chunks_created=chunk_count,
            )
        )
    return OfflineDocumentsResponse(items=items)


@router.post(
    "/offline-documents",
    response_model=OfflineDocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_offline_document(
    db: DatabaseSession,
    tenant: CurrentTenant,
    _principal: CurrentPrincipal,
    _authz: ManageAuthz,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
):
    """Parse a bounded local file into lexical demo chunks without embeddings."""
    _require_local_offline_demo()
    filename = file.filename or "document"
    content = await file.read(MAX_LOCAL_DOCUMENT_BYTES + 1)
    if len(content) > MAX_LOCAL_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 512 KiB local limit")
    try:
        text = DocumentParserService.parse(filename=filename, content=content)
    except UnsupportedDocumentTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Document could not be parsed") from exc
    cleaned = KnowledgeIngestionService.clean_text(text)
    if not cleaned:
        raise HTTPException(status_code=422, detail="No readable text found in document")
    checksum = KnowledgeIngestionService.checksum(cleaned)
    existing = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.organization_id == tenant.organization_id,
                KnowledgeDocument.source == LOCAL_DOCUMENT_SOURCE,
                KnowledgeDocument.checksum == checksum,
            )
        )
    ).scalar_one_or_none()
    if existing:
        chunk_count = len(
            (
                await db.execute(
                    select(KnowledgeChunk.id).where(
                        KnowledgeChunk.organization_id == tenant.organization_id,
                        KnowledgeChunk.document_id == existing.id,
                    )
                )
            ).scalars().all()
        )
        return OfflineDocumentUploadResponse(
            document_id=existing.id,
            title=existing.title,
            filename=(existing.metadata_json or {}).get("filename", filename),
            chunks_created=chunk_count,
            duplicate=True,
        )
    document_count = int(
        await db.scalar(
            select(func.count(KnowledgeDocument.id)).where(
                KnowledgeDocument.organization_id == tenant.organization_id,
                KnowledgeDocument.source == LOCAL_DOCUMENT_SOURCE,
            )
        )
        or 0
    )
    if document_count >= MAX_LOCAL_DOCUMENTS:
        raise HTTPException(status_code=409, detail="Local document limit reached")
    fallback_title = filename.rsplit(".", 1)[0].strip() or "document"
    document_title = (title or fallback_title).strip()[:160] or fallback_title[:160]
    document = KnowledgeDocument(
        organization_id=tenant.organization_id,
        title=document_title,
        source=LOCAL_DOCUMENT_SOURCE,
        source_uri=filename,
        checksum=checksum,
        metadata_json={"filename": filename, "demo": True, "synthetic": True},
    )
    db.add(document)
    await db.flush()
    chunks = KnowledgeIngestionService.chunk_text(cleaned)
    db.add_all(
        KnowledgeChunk(
            organization_id=tenant.organization_id,
            document_id=document.id,
            chunk_index=index,
            content=chunk_content,
            token_count=token_count,
            metadata_json={
                "filename": filename,
                "title": document.title,
                "demo": True,
                "synthetic": True,
                "chunk_index": index,
            },
            embedding=[0.0] * 1536,
        )
        for index, (chunk_content, token_count) in enumerate(chunks)
    )
    await db.commit()
    return OfflineDocumentUploadResponse(
        document_id=document.id,
        title=document.title,
        filename=filename,
        chunks_created=len(chunks),
        duplicate=False,
    )


@router.delete("/offline-documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_offline_document(
    document_id: int,
    db: DatabaseSession,
    tenant: CurrentTenant,
    _principal: CurrentPrincipal,
    _authz: ManageAuthz,
):
    """Delete only a local synthetic document owned by the resolved tenant."""
    _require_local_offline_demo()
    document = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.organization_id == tenant.organization_id,
                KnowledgeDocument.source == LOCAL_DOCUMENT_SOURCE,
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await db.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.organization_id == tenant.organization_id,
            KnowledgeChunk.document_id == document.id,
        )
    )
    await db.delete(document)
    await db.commit()


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
