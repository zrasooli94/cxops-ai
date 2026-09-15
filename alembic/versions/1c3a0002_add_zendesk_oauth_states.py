"""add zendesk oauth states

Revision ID: 1c3a0002
Revises: 1c3a0001
Create Date: 2026-09-15 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3a0002"
down_revision: str | Sequence[str] | None = "1c3a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create durable, organization-bound OAuth state storage."""
    op.create_table(
        "zendesk_oauth_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_zendesk_oauth_states_state"),
        "zendesk_oauth_states",
        ["state"],
        unique=True,
    )

    op.create_index(
        op.f("ix_zendesk_oauth_states_organization_id"),
        "zendesk_oauth_states",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the OAuth state table."""
    op.drop_index(
        op.f("ix_zendesk_oauth_states_organization_id"),
        table_name="zendesk_oauth_states",
    )
    op.drop_index(
        op.f("ix_zendesk_oauth_states_state"),
        table_name="zendesk_oauth_states",
    )
    op.drop_table("zendesk_oauth_states")
