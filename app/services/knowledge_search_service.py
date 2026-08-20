from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.knowledge_repository import (
    KnowledgeRepository,
)
from app.services.embedding_service import (
    embedding_service,
)


class KnowledgeSearchService:
    @staticmethod
    async def search(
        db: AsyncSession,
        *,
        query: str,
        limit: int = 5,
    ) -> list[dict]:

        query_embedding = await embedding_service.embed_text(query)

        matches = await KnowledgeRepository.semantic_search(
            db=db,
            embedding=query_embedding,
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
