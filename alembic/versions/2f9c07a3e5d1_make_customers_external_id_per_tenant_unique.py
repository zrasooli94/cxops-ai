"""make customers.external_id unique per tenant

Revision ID: 2f9c07a3e5d1
Revises: 8374ad62c1b4
Create Date: 2026-09-15 10:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f9c07a3e5d1"
down_revision: str | Sequence[str] | None = "8374ad62c1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Customers are resolved per tenant (never globally by external_id), so the
    # global unique constraint only caused cross-tenant create contention.
    op.drop_constraint(
        "customers_external_id_key",
        "customers",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("ux_customers_organization_id_external_id"),
        "customers",
        ["organization_id", "external_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("ux_customers_organization_id_external_id"),
        "customers",
        type_="unique",
    )
    op.create_unique_constraint(
        "customers_external_id_key",
        "customers",
        ["external_id"],
    )