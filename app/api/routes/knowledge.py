from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenant
from app.core.database import get_db
from app.repositories.knowledge_repository import (
    KnowledgeRepository,
)
from app.schemas.knowledge import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentSummary,
    KnowledgeFileIngestionResult,
    KnowledgeIngestionResult,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    RAGAnswerRequest,
    RAGAnswerResponse,
)
from app.services.document_parser_service import (
    DocumentParserService,
    UnsupportedDocumentTypeError,
)
from app.services.knowledge_ingestion_service import (
    KnowledgeIngestionService,
)
from app.services.knowledge_search_service import (
    KnowledgeSearchService,
)
from app.services.rag_service import (
    rag_service,
)

router = APIRouter(
    prefix="/knowledge",
    tags=["Knowledge Base"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


def serialize_document(
    document,
) -> dict:
    return {
        "document_id": document.id,
        "title": document.title,
        "source": document.source,
        "source_uri": document.source_uri,
        "checksum": document.checksum,
        "metadata": document.metadata_json,
        "created_at": document.created_at,
    }


@router.post(
    "/documents",
    response_model=KnowledgeIngestionResult,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_document(
    data: KnowledgeDocumentCreate,
    db: DatabaseSession,
    tenant: CurrentTenant,
):
    return await KnowledgeIngestionService.ingest(
        db=db,
        organization_id=tenant.organization_id,
        title=data.title,
        content=data.content,
        source=data.source,
        source_uri=data.source_uri,
        metadata=data.metadata,
    )


@router.get(
    "/documents",
    response_model=list[KnowledgeDocumentSummary],
)
async def list_documents(
    db: DatabaseSession,
    tenant: CurrentTenant,
    offset: int = 0,
    limit: int = 100,
):
    safe_limit = max(
        1,
        min(limit, 200),
    )

    documents = await KnowledgeRepository.list_documents_for_tenant(
        db,
        organization_id=tenant.organization_id,
        offset=offset,
        limit=safe_limit,
    )

    return [serialize_document(document) for document in documents]


@router.get(
    "/documents/{document_id}",
    response_model=KnowledgeDocumentSummary,
)
async def get_document(
    document_id: int,
    db: DatabaseSession,
    tenant: CurrentTenant,
):
    document = await KnowledgeRepository.get_document_for_tenant(
        db,
        document_id=document_id,
        organization_id=tenant.organization_id,
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return serialize_document(document)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: int,
    db: DatabaseSession,
    tenant: CurrentTenant,
):
    deleted = await KnowledgeRepository.delete_document_for_tenant(
        db,
        document_id=document_id,
        organization_id=tenant.organization_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )


@router.post(
    "/search",
    response_model=list[KnowledgeSearchResult],
)
async def search_knowledge(
    data: KnowledgeSearchRequest,
    db: DatabaseSession,
    tenant: CurrentTenant,
):
    return await KnowledgeSearchService.search(
        db=db,
        organization_id=tenant.organization_id,
        query=data.query,
        limit=data.limit,
    )


@router.post(
    "/documents/upload",
    response_model=KnowledgeFileIngestionResult,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    db: DatabaseSession,
    tenant: CurrentTenant,
    file: Annotated[UploadFile, File()],
    title: str | None = Form(default=None),
    source: str = Form(default="uploaded-file"),
):
    filename = file.filename or "document"

    content = await file.read()

    try:
        text = DocumentParserService.parse(
            filename=filename,
            content=content,
        )

    except UnsupportedDocumentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        )

    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No readable text found in document",
        )

    document_title = title or filename.rsplit(".", 1)[0]

    result = await KnowledgeIngestionService.ingest(
        db=db,
        organization_id=tenant.organization_id,
        title=document_title,
        content=text,
        source=source,
        source_uri=filename,
        metadata={
            "filename": filename,
            "content_type": file.content_type,
        },
    )

    return {
        **result,
        "filename": filename,
    }


@router.post(
    "/answer",
    response_model=RAGAnswerResponse,
)
async def answer_from_knowledge(
    data: RAGAnswerRequest,
    db: DatabaseSession,
    tenant: CurrentTenant,
):
    return await rag_service.answer(
        db=db,
        organization_id=tenant.organization_id,
        question=data.question,
        top_k=data.top_k,
    )