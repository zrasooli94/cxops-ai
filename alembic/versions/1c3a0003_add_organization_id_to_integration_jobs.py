"""add organization id to integration jobs

Revision ID: 1c3a0003
Revises: 1c3a0002
Create Date: 2026-09-15 00:00:02.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3a0003"
down_revision: str | Sequence[str] | None = "1c3a0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Bind integration jobs to their originating organization."""
    op.add_column(
        "integration_jobs",
        sa.Column(
            "organization_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        op.f("fk_integration_jobs_organization_id_organizations"),
        "integration_jobs",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index(
        op.f("ix_integration_jobs_organization_id"),
        "integration_jobs",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the organization binding from integration jobs."""
    op.drop_index(
        op.f("ix_integration_jobs_organization_id"),
        table_name="integration_jobs",
    )
    op.drop_constraint(
        op.f("fk_integration_jobs_organization_id_organizations"),
        "integration_jobs",
        type_="foreignkey",
    )
    op.drop_column("integration_jobs", "organization_id")
