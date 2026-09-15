"""make ticket external id tenant unique

Revision ID: 1c3a0005
Revises: 1c3a0004
Create Date: 2026-09-15 00:00:04.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3a0005"
down_revision: str | Sequence[str] | None = "1c3a0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Scope ticket external_id uniqueness to the owning organization.

    The old global ``tickets_external_id_key`` is replaced by
    ``(organization_id, external_id)`` so distinct organizations can safely
    reference the same provider ticket id. NULL organization rows (legacy,
    pre-tenant webhook ingestion) remain exempt under NULL-distinct semantics.
    """
    op.drop_constraint(
        "tickets_external_id_key",
        "tickets",
        type_="unique",
    )

    op.create_unique_constraint(
        op.f("ux_tickets_organization_id_external_id"),
        "tickets",
        ["organization_id", "external_id"],
    )


def downgrade() -> None:
    """Restore the global unique ticket external_id constraint."""
    op.drop_constraint(
        op.f("ux_tickets_organization_id_external_id"),
        "tickets",
        type_="unique",
    )

    op.create_unique_constraint(
        "tickets_external_id_key",
        "tickets",
        ["external_id"],
    )
