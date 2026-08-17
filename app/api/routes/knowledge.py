from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.knowledge import (
    KnowledgeDocumentCreate,
    KnowledgeIngestionResult,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from app.services.knowledge_ingestion_service import (
    KnowledgeIngestionService,
)
from app.services.knowledge_search_service import (
    KnowledgeSearchService,
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