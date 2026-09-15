"""knowledge tenant isolation

Revision ID: 1c3b0001
Revises: 1c3a0005
Create Date: 2026-09-15 00:00:06.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3b0001"
down_revision: str | Sequence[str] | None = "1c3a0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make knowledge documents and chunks organization-owned (staged).

    Staged legacy migration: ``organization_id`` is added NULL on both
    ``knowledge_documents`` and ``knowledge_chunks`` so pre-existing global rows
    are never arbitrarily assigned to an organization. No backfill runs here.
    Legacy NULL-org rows are deliberately inert: no tenant-facing path can list,
    search, cite, or dedupe against them.

    The global checksum uniqueness (``ix_knowledge_documents_checksum``) is
    replaced by a tenant-composite unique constraint
    ``(organization_id, checksum)``. Postgres NULL-distinct semantics keep NULL
    legacy rows exempt, while distinct organizations may legitimately own
    identical content.

    Only indexes justified by the query shape are added: a btree index on each
    ``organization_id`` column, because retrieval is gated by
    ``WHERE organization_id = :org`` before ranking. No vector index is created
    in this phase: correctness of the tenant predicate is the requirement, and
    the existing global vector corpus has no index today (sequential scan).
    """
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "organization_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "organization_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        op.f("fk_knowledge_documents_organization_id_organizations"),
        "knowledge_documents",
        "organizations",
        ["organization_id"],
        ["id"],
    )

    op.create_foreign_key(
        op.f("fk_knowledge_chunks_organization_id_organizations"),
        "knowledge_chunks",
        "organizations",
        ["organization_id"],
        ["id"],
    )

    op.create_index(
        op.f("ix_knowledge_documents_organization_id"),
        "knowledge_documents",
        ["organization_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_knowledge_chunks_organization_id"),
        "knowledge_chunks",
        ["organization_id"],
        unique=False,
    )

    op.drop_index(
        op.f("ix_knowledge_documents_checksum"),
        table_name="knowledge_documents",
    )

    op.create_unique_constraint(
        op.f("ux_knowledge_documents_organization_id_checksum"),
        "knowledge_documents",
        ["organization_id", "checksum"],
    )


def downgrade() -> None:
    """Revert to the global knowledge model."""
    op.drop_constraint(
        op.f("ux_knowledge_documents_organization_id_checksum"),
        "knowledge_documents",
        type_="unique",
    )

    op.create_index(
        op.f("ix_knowledge_documents_checksum"),
        "knowledge_documents",
        ["checksum"],
        unique=True,
    )

    op.drop_index(
        op.f("ix_knowledge_chunks_organization_id"),
        table_name="knowledge_chunks",
    )

    op.drop_index(
        op.f("ix_knowledge_documents_organization_id"),
        table_name="knowledge_documents",
    )

    op.drop_constraint(
        op.f("fk_knowledge_chunks_organization_id_organizations"),
        "knowledge_chunks",
        type_="foreignkey",
    )

    op.drop_constraint(
        op.f("fk_knowledge_documents_organization_id_organizations"),
        "knowledge_documents",
        type_="foreignkey",
    )

    op.drop_column("knowledge_chunks", "organization_id")
    op.drop_column("knowledge_documents", "organization_id")