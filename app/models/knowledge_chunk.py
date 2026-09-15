from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class KnowledgeChunk(Base):
    """A tenant-owned searchable chunk.

    ``organization_id`` duplicates the owner column on the chunk (rather than
    resolving through the document) so the vector-retrieval query can enforce
    ``WHERE organization_id = :org`` directly on the table being ranked by
    embedding distance. Tenant filtering is fused into the SQL query before
    nearest-neighbor selection — never applied as a Python post-filter.

    Because the owner is duplicated, drift between chunk and document ownership
    is prevented at the database: the composite foreign key
    ``(document_id, organization_id) → knowledge_documents(id, organization_id)``
    makes it impossible to attach a chunk to a document owned by a different
    tenant. The direct ``organization_id`` column is preserved for vector
    filtering.
    """

    __tablename__ = "knowledge_chunks"

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["knowledge_documents.id", "knowledge_documents.organization_id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_document_organization",
        ),
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_knowledge_chunk_document_index",
        ),
        Index(
            "ix_knowledge_chunks_organization_id",
            "organization_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=True,
    )

    document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    metadata_json: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    embedding: Mapped[list[float]] = mapped_column(
        VECTOR(1536),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    document = relationship(
        "KnowledgeDocument",
        back_populates="chunks",
    )