"""make customer email tenant unique

Revision ID: 1c3a0004
Revises: 1c3a0003
Create Date: 2026-09-15 00:00:03.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3a0004"
down_revision: str | Sequence[str] | None = "1c3a0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Scope customer email uniqueness to the owning organization.

    The old global ``customers_email_key`` is removed in favor of
    ``(organization_id, email)``, so the same email can exist safely in two
    organizations while remaining unique within one. Rows with NULL
    organization_id are exempt under PostgreSQL NULL-distinct semantics.

    Fail-safe: if existing data already contains a duplicate ``(org, email)``
    pair the additive constraint fails and the upgrade stops rather than
    rewriting ownership.
    """
    op.drop_constraint(
        "customers_email_key",
        "customers",
        type_="unique",
    )

    op.create_unique_constraint(
        op.f("ux_customers_organization_id_email"),
        "customers",
        ["organization_id", "email"],
    )


def downgrade() -> None:
    """Restore the global unique customer email constraint."""
    op.drop_constraint(
        op.f("ux_customers_organization_id_email"),
        "customers",
        type_="unique",
    )

    op.create_unique_constraint(
        "customers_email_key",
        "customers",
        ["email"],
    )
