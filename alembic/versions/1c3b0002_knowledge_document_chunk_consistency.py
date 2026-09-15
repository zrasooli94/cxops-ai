"""knowledge document/chunk tenant consistency

Revision ID: 1c3b0002
Revises: 1c3b0001
Create Date: 2026-09-15 00:00:07.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3b0002"
down_revision: str | Sequence[str] | None = "1c3b0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enforce tenant-consistent document/chunk ownership in the database.

    ``organization_id`` is intentionally duplicated onto ``knowledge_chunks``
    so the vector-retrieval query can filter on the table being ranked. That
    duplication must not be able to drift from the owning document, otherwise a
    chunk owned by Org A could cite an Org B document as RAG evidence.

    This migration makes the invariant a database constraint:

    - ``UNIQUE (id, organization_id)`` on ``knowledge_documents`` — the
      composite target for the chunk FK (``id`` alone is already the PK, so
      this is redundant for uniqueness and exists solely as the FK target).
    - the chunk's document reference becomes a composite foreign key
      ``(document_id, organization_id) → knowledge_documents(id, organization_id)``
      with ``ON DELETE CASCADE``, replacing the single-column FK on
      ``document_id``.

    A mismatched chunk — ``document_id`` referencing an Org B document while
    ``organization_id`` is Org A — now violates the composite FK and cannot be
    inserted. Postgres MATCH SIMPLE semantics leave NULL-org legacy chunks
    exempt, matching the staged-legacy design: NULL rows remain inert and must
    be migrated explicitly in a later cleanup.
    """
    op.create_unique_constraint(
        op.f("ux_knowledge_documents_id_organization_id"),
        "knowledge_documents",
        ["id", "organization_id"],
    )

    op.drop_constraint(
        "knowledge_chunks_document_id_fkey",
        "knowledge_chunks",
        type_="foreignkey",
    )

    op.create_foreign_key(
        op.f("fk_knowledge_chunks_document_organization"),
        "knowledge_chunks",
        "knowledge_documents",
        ["document_id", "organization_id"],
        ["id", "organization_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Revert to the single-column document FK (dropping tenant consistency)."""
    op.drop_constraint(
        op.f("fk_knowledge_chunks_document_organization"),
        "knowledge_chunks",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "knowledge_chunks_document_id_fkey",
        "knowledge_chunks",
        "knowledge_documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        op.f("ux_knowledge_documents_id_organization_id"),
        "knowledge_documents",
        type_="unique",
    )