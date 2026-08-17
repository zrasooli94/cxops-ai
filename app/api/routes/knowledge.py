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

from app.core.database import get_db
from app.schemas.knowledge import (
    KnowledgeDocumentCreate,
    KnowledgeFileIngestionResult,
    KnowledgeIngestionResult,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    RAGAnswerRequest,
    RAGAnswerResponse,
)
from app.services.rag_service import (
    rag_service,
)
from app.services.knowledge_ingestion_service import (
    KnowledgeIngestionService,
)
from app.services.knowledge_search_service import (
    KnowledgeSearchService,
)
from app.services.document_parser_service import (
    DocumentParserService,
    UnsupportedDocumentTypeError,
)

router = APIRouter(
    prefix="/knowledge",
    tags=["Knowledge Base"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


@router.post(
    "/documents",
    response_model=KnowledgeIngestionResult,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_document(
    data: KnowledgeDocumentCreate,
    db: DatabaseSession,
):
    return await KnowledgeIngestionService.ingest(
        db=db,
        title=data.title,
        content=data.content,
        source=data.source,
        source_uri=data.source_uri,
        metadata=data.metadata,
    )


@router.post(
    "/search",
    response_model=list[KnowledgeSearchResult],
)
async def search_knowledge(
    data: KnowledgeSearchRequest,
    db: DatabaseSession,
):
    return await KnowledgeSearchService.search(
        db=db,
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
    file: UploadFile = File(...),
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

    document_title = (
        title
        or filename.rsplit(".", 1)[0]
    )

    result = await KnowledgeIngestionService.ingest(
        db=db,
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
):
    return await rag_service.answer(
        db=db,
        question=data.question,
        top_k=data.top_k,
    )