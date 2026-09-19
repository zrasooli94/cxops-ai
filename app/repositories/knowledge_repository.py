from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument


class KnowledgeRepository:
    """Tenant-safe knowledge access.

    Every read method takes the trusted ``organization_id`` and enforces it in
    the SQL predicate. There is deliberately NO global/unscoped reader: an
    unowned document or chunk must be unreachable by every code path in the
    application. Legacy NULL-org rows fail every tenant predicate.
    """

    @staticmethod
    def semantic_search_statement(
        *,
        embedding: list[float],
        organization_id: int,
        limit: int = 5,
    ):
        """Build the vector-retrieval SELECT with the tenant predicate fused in.

        The ``organization_id`` predicate is part of the SQL statement before
        ranking: only chunks owned by the organization are eligible for the
        top-k, so a foreign tenant's near-perfect match can never consume a
        rank slot and then be filtered away in Python.
        """
        distance = KnowledgeChunk.embedding.cosine_distance(embedding)

        return (
            select(
                KnowledgeChunk,
                distance.label("distance"),
            )
            .where(KnowledgeChunk.organization_id == organization_id)
            .order_by(distance)
            .limit(limit)
        )

    @staticmethod
    async def get_document_by_checksum_for_tenant(
        db: AsyncSession,
        *,
        checksum: str,
        organization_id: int,
    ) -> KnowledgeDocument | None:

        result = await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.checksum == checksum,
                KnowledgeDocument.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def list_documents_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        offset: int = 0,
        limit: int = 100,
    ) -> list[KnowledgeDocument]:

        result = await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.organization_id == organization_id)
            .order_by(KnowledgeDocument.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        return list(result.scalars().all())

    @staticmethod
    async def summary_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> dict[str, Any]:
        """Tenant-scoped operational summary of the knowledge corpus.

        Returns counts and the latest ingestion timestamp without exposing raw
        document content. The embedding/vector model is reported from settings.
        """
        doc_result = await db.execute(
            select(
                func.count(KnowledgeDocument.id).label("document_count"),
                func.max(KnowledgeDocument.created_at).label("last_ingestion_at"),
            ).where(KnowledgeDocument.organization_id == organization_id)
        )
        doc_row = doc_result.mappings().one()

        chunk_result = await db.execute(
            select(func.count(KnowledgeChunk.id).label("chunk_count")).where(
                KnowledgeChunk.organization_id == organization_id
            )
        )
        chunk_row = chunk_result.mappings().one()

        last_ingestion_at = doc_row["last_ingestion_at"]
        last_ingestion_iso = (
            last_ingestion_at.isoformat()
            if last_ingestion_at is not None
            else ""
        )

        return {
            "document_count": int(doc_row["document_count"] or 0),
            "chunk_count": int(chunk_row["chunk_count"] or 0),
            "last_ingestion_at": last_ingestion_at,
            "embedding_model": "text-embedding-3-small",
            "corpus_revision": (
                f"docs:{doc_row['document_count'] or 0}:"
                f"chunks:{chunk_row['chunk_count'] or 0}:"
                f"updated:{last_ingestion_iso}"
            ),
        }

    @staticmethod
    async def get_document_for_tenant(
        db: AsyncSession,
        *,
        document_id: int,
        organization_id: int,
    ) -> KnowledgeDocument | None:

        result = await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.organization_id == organization_id,
            )
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
    async def semantic_search_for_tenant(
        db: AsyncSession,
        *,
        embedding: list[float],
        organization_id: int,
        limit: int = 5,
    ) -> list[tuple[KnowledgeChunk, float]]:

        statement = KnowledgeRepository.semantic_search_statement(
            embedding=embedding,
            organization_id=organization_id,
            limit=limit,
        )

        result = await db.execute(statement)

        rows = result.all()

        return [
            (
                row[0],
                float(row[1]),
            )
            for row in rows
        ]

    @staticmethod
    async def delete_document_for_tenant(
        db: AsyncSession,
        *,
        document_id: int,
        organization_id: int,
    ) -> bool:
        """Delete a document only when it belongs to the organization.

        Returns ``False`` (no deletion) for both a missing id and a foreign
        tenant's id, so callers can map both to 404 without an existence oracle.
        """

        document = await KnowledgeRepository.get_document_for_tenant(
            db,
            document_id=document_id,
            organization_id=organization_id,
        )

        if document is None:
            return False

        await db.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.document_id == document_id,
                KnowledgeChunk.organization_id == organization_id,
            )
        )

        await db.execute(
            delete(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.organization_id == organization_id,
            )
        )

        await db.commit()

        return True