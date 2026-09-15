"""add organization memberships

Revision ID: 4fb59ecef3ee
Revises: 7f3d40f68b79
Create Date: 2026-09-14 16:14:30.269830

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4fb59ecef3ee"
down_revision: str | Sequence[str] | None = "7f3d40f68b79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_organization_memberships_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "subject",
            name="uq_organization_memberships_organization_id_subject",
        ),
    )
    op.create_index(
        op.f("ix_organization_memberships_subject"),
        "organization_memberships",
        ["subject"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_organization_memberships_subject"),
        table_name="organization_memberships",
    )
    op.drop_table("organization_memberships")
