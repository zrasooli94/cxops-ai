from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument


class KnowledgeRepository:
    @staticmethod
    async def get_document_by_checksum(
        db: AsyncSession,
        checksum: str,
    ) -> KnowledgeDocument | None:

        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.checksum == checksum)
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def create_document(
        db: AsyncSession,
        document: KnowledgeDocument,
    ) -> KnowledgeDocument:

        db.add(document)

        await db.commit()
        await db.refresh(document)

        return document

    @staticmethod
    async def create_chunks(
        db: AsyncSession,
        chunks: list[KnowledgeChunk],
    ) -> list[KnowledgeChunk]:

        db.add_all(chunks)

        await db.commit()

        for chunk in chunks:
            await db.refresh(chunk)

        return chunks

    @staticmethod
    async def semantic_search(
        db: AsyncSession,
        embedding: list[float],
        limit: int = 5,
    ) -> list[tuple[KnowledgeChunk, float]]:

        distance = KnowledgeChunk.embedding.cosine_distance(embedding)

        result = await db.execute(
            select(
                KnowledgeChunk,
                distance.label("distance"),
            )
            .order_by(distance)
            .limit(limit)
        )

        rows = result.all()
        return [
            (
                row[0],
                float(row[1]),
            )
            for row in rows
        ]
