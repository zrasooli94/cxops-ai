from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.knowledge_repository import (
    KnowledgeRepository,
)
from app.services.embedding_service import (
    embedding_service,
)


class KnowledgeSearchService:
    @staticmethod
    def _require_organization_id(
        organization_id: int | None,
    ) -> int:
        if organization_id is None:
            raise ValueError("organization_id is required for knowledge search")

        return organization_id

    @staticmethod
    async def search(
        db: AsyncSession,
        *,
        organization_id: int,
        query: str,
        limit: int = 5,
    ) -> list[dict]:

        organization_id = KnowledgeSearchService._require_organization_id(
            organization_id
        )

        query_embedding = await embedding_service.embed_text(query)

        matches = await KnowledgeRepository.semantic_search_for_tenant(
            db=db,
            embedding=query_embedding,
            organization_id=organization_id,
            limit=limit,
        )

        results = []

        for chunk, distance in matches:
            similarity = 1.0 - distance

            results.append(
                {
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "content": chunk.content,
                    "distance": distance,
                    "similarity": similarity,
                    "metadata": (chunk.metadata_json),
                }
            )

        return results