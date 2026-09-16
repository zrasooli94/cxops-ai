"""add membership roles

Revision ID: 1d100001
Revises: 1c3d0001
Create Date: 2026-09-16 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d100001"
down_revision: str | Sequence[str] | None = "1c3d0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VALID_ROLES = ("owner", "admin", "supervisor", "agent", "viewer")


def upgrade() -> None:
    """Add role column, backfill deterministically, enforce validity."""
    op.add_column(
        "organization_memberships",
        sa.Column("role", sa.String(length=20), nullable=True),
    )

    # Backfill policy (Phase 1D.1 transition):
    # - Organizations with exactly one membership were created before RBAC and
    #   implied sole ownership, so that member becomes owner.
    # - Organizations with multiple pre-RBAC memberships have no reliable
    #   evidence of which subject should own the organization, so they are
    #   downgraded to viewer (safest deterministic default). A later owner can
    #   promote them through member management once Phase 1D.2/1D.4 ships.
    op.execute(
        """
        UPDATE organization_memberships
        SET role = CASE
          WHEN organization_id IN (
            SELECT organization_id
            FROM organization_memberships
            GROUP BY organization_id
            HAVING COUNT(*) = 1
          ) THEN 'owner'
          ELSE 'viewer'
        END
        WHERE role IS NULL
        """
    )

    op.alter_column(
        "organization_memberships",
        "role",
        nullable=False,
    )

    op.create_check_constraint(
        "ck_organization_memberships_role_valid",
        "organization_memberships",
        sa.text(f"role IN {VALID_ROLES}"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_organization_memberships_role_valid",
        "organization_memberships",
        type_="check",
    )
    op.drop_column("organization_memberships", "role")
