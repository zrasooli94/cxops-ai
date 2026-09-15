from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class KnowledgeDocument(Base):
    """A tenant-owned knowledge document.

    ``organization_id`` is the tenant boundary. Every tenant-facing read starts
    from a resolved organization_id and enforces the predicate in SQL; NULL-org
    rows are legacy, unmapped documents that remain **inert** — never listed,
    searched, cited, or returned as a dedupe match.

    ``UNIQUE (id, organization_id)`` is the composite target for
    ``knowledge_chunks``' tenant-consistent document FK: a chunk may only
    reference a document that carries the same ``organization_id``.
    """

    __tablename__ = "knowledge_documents"

    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_knowledge_documents_id_organization_id",
        ),
        UniqueConstraint(
            "organization_id",
            "checksum",
            name="ux_knowledge_documents_organization_id_checksum",
        ),
        Index(
            "ix_knowledge_documents_organization_id",
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

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    source: Mapped[str] = mapped_column(
        String(100),
        default="manual",
        nullable=False,
    )

    source_uri: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    checksum: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    metadata_json: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chunks = relationship(
        "KnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan",
    )