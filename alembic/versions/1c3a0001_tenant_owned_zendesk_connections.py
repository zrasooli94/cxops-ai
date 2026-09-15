"""tenant owned zendesk connections

Revision ID: 1c3a0001
Revises: 2f9c07a3e5d1
Create Date: 2026-09-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3a0001"
down_revision: str | Sequence[str] | None = "2f9c07a3e5d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make Zendesk OAuth credentials organization-owned.

    Legacy global tokens keep ``organization_id`` NULL and are deliberately
    inert: no code path resolves or refreshes them. No backfill is performed so
    no arbitrary organization is ever assigned.
    """
    op.add_column(
        "zendesk_oauth_tokens",
        sa.Column(
            "organization_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        op.f("fk_zendesk_oauth_tokens_organization_id_organizations"),
        "zendesk_oauth_tokens",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_unique_constraint(
        op.f("ux_zendesk_oauth_tokens_organization_id"),
        "zendesk_oauth_tokens",
        ["organization_id"],
    )

    op.add_column(
        "zendesk_oauth_tokens",
        sa.Column(
            "integration_id",
            sa.String(length=36),
            nullable=True,
        ),
    )

    op.create_index(
        op.f("ix_zendesk_oauth_tokens_integration_id"),
        "zendesk_oauth_tokens",
        ["integration_id"],
        unique=True,
    )

    op.add_column(
        "zendesk_oauth_tokens",
        sa.Column(
            "webhook_secret",
            sa.Text(),
            nullable=True,
        ),
    )

    op.add_column(
        "zendesk_oauth_tokens",
        sa.Column(
            "connected_by",
            sa.String(length=255),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Revert to the global Zendesk credential model."""
    op.drop_column("zendesk_oauth_tokens", "connected_by")
    op.drop_column("zendesk_oauth_tokens", "webhook_secret")
    op.drop_index(
        op.f("ix_zendesk_oauth_tokens_integration_id"),
        table_name="zendesk_oauth_tokens",
    )
    op.drop_column("zendesk_oauth_tokens", "integration_id")
    op.drop_constraint(
        op.f("ux_zendesk_oauth_tokens_organization_id"),
        "zendesk_oauth_tokens",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_zendesk_oauth_tokens_organization_id_organizations"),
        "zendesk_oauth_tokens",
        type_="foreignkey",
    )
    op.drop_column("zendesk_oauth_tokens", "organization_id")
