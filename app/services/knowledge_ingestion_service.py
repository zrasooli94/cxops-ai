import hashlib
import re

import tiktoken
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.knowledge_repository import (
    KnowledgeRepository,
)
from app.services.embedding_service import (
    embedding_service,
)


class KnowledgeIngestionService:

    CHUNK_SIZE = 500
    CHUNK_OVERLAP = 75

    @staticmethod
    def clean_text(
        text: str,
    ) -> str:

        text = text.replace("\r\n", "\n")

        text = re.sub(
            r"[ \t]+",
            " ",
            text,
        )

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip()

    @staticmethod
    def checksum(
        text: str,
    ) -> str:

        return hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def chunk_text(
        text: str,
    ) -> list[tuple[str, int]]:

        encoding = tiktoken.get_encoding(
            "cl100k_base"
        )

        tokens = encoding.encode(text)

        chunks: list[tuple[str, int]] = []

        start = 0

        while start < len(tokens):

            end = min(
                start
                + KnowledgeIngestionService.CHUNK_SIZE,
                len(tokens),
            )

            chunk_tokens = tokens[start:end]

            chunk_text = encoding.decode(
                chunk_tokens
            ).strip()

            if chunk_text:
                chunks.append(
                    (
                        chunk_text,
                        len(chunk_tokens),
                    )
                )

            if end >= len(tokens):
                break

            start = (
                end
                - KnowledgeIngestionService.CHUNK_OVERLAP
            )

        return chunks

    @staticmethod
    async def ingest(
        db: AsyncSession,
        *,
        title: str,
        content: str,
        source: str,
        source_uri: str | None,
        metadata: dict,
    ):

        cleaned_content = (
            KnowledgeIngestionService.clean_text(
                content
            )
        )

        checksum = (
            KnowledgeIngestionService.checksum(
                cleaned_content
            )
        )

        existing = (
            await KnowledgeRepository.get_document_by_checksum(
                db=db,
                checksum=checksum,
            )
        )

        if existing:
            return {
                "document_id": existing.id,
                "title": existing.title,
                "chunks_created": 0,
                "duplicate": True,
            }

        text_chunks = (
            KnowledgeIngestionService.chunk_text(
                cleaned_content
            )
        )

        chunk_contents = [
            chunk[0]
            for chunk in text_chunks
        ]

        embeddings = (
            await embedding_service.embed_documents(
                chunk_contents
            )
        )

        document = KnowledgeDocument(
            title=title,
            source=source,
            source_uri=source_uri,
            checksum=checksum,
            metadata_json=metadata,
        )

        db.add(document)
        await db.flush()

        chunks = []

        for index, (
            (chunk_content, token_count),
            embedding,
        ) in enumerate(
            zip(
                text_chunks,
                embeddings,
                strict=True,
            )
        ):
            chunks.append(
                KnowledgeChunk(
                    document_id=document.id,
                    chunk_index=index,
                    content=chunk_content,
                    token_count=token_count,
                    metadata_json={
                        **metadata,
                        "title": title,
                        "source": source,
                        "chunk_index": index,
                    },
                    embedding=embedding,
                )
            )

        db.add_all(chunks)

        await db.commit()
        await db.refresh(document)

        return {
            "document_id": document.id,
            "title": document.title,
            "chunks_created": len(chunks),
            "duplicate": False,
        }