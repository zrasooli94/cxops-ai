"""add tickets.organization_id and customer indexes

Revision ID: 88692247ae67
Revises: 4fb59ecef3ee
Create Date: 2026-09-14 17:08:42.837927

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "88692247ae67"
down_revision: str | Sequence[str] | None = "4fb59ecef3ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add tickets.organization_id as nullable FK
    op.add_column(
        "tickets",
        sa.Column("organization_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_tickets_organization_id_organizations"),
        "tickets",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_tickets_organization_id"),
        "tickets",
        ["organization_id"],
        unique=False,
    )

    # Backfill tickets.organization_id from customers.organization_id
    op.execute(
        """
        UPDATE tickets
        SET organization_id = customers.organization_id
        FROM customers
        WHERE tickets.customer_id = customers.id
          AND tickets.organization_id IS NULL
          AND customers.organization_id IS NOT NULL
        """
    )

    # Ensure customers.organization_id has an index (FK may not have auto-created one)
    # Note: The FK constraint on customers.organization_id was created in 64fd6087fc0e
    # but let's ensure the index exists for tenant filtering performance.
    op.create_index(
        op.f("ix_customers_organization_id"),
        "customers",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_customers_organization_id"),
        table_name="customers",
    )
    op.drop_index(
        op.f("ix_tickets_organization_id"),
        table_name="tickets",
    )
    op.drop_constraint(
        op.f("fk_tickets_organization_id_organizations"),
        "tickets",
        type_="foreignkey",
    )
    op.drop_column("tickets", "organization_id")