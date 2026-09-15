"""add automation_rules.organization_id

Revision ID: 8374ad62c1b4
Revises: 88692247ae67
Create Date: 2026-09-15 10:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8374ad62c1b4"
down_revision: str | Sequence[str] | None = "88692247ae67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add automation_rules.organization_id as nullable FK.
    #
    # Legacy/global rules are intentionally left NULL: there is no safe way to
    # assign historical rules to an arbitrary organization. NULL-org rules are
    # inert for tenant-owned ticket automation.
    op.add_column(
        "automation_rules",
        sa.Column("organization_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_automation_rules_organization_id_organizations"),
        "automation_rules",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_automation_rules_organization_id"),
        "automation_rules",
        ["organization_id"],
        unique=False,
    )

    # Rule names become unique per organization (NULL-org legacy rows remain
    # accepted under PostgreSQL's NULL-distinct semantics). No rule is ever
    # looked up globally by name, so this composite unique is unambiguous.
    op.drop_constraint(
        "automation_rules_name_key",
        "automation_rules",
        type_="unique",
    )
    op.create_index(
        op.f("ux_automation_rules_organization_id_name"),
        "automation_rules",
        ["organization_id", "name"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ux_automation_rules_organization_id_name"),
        table_name="automation_rules",
    )
    op.create_unique_constraint(
        "automation_rules_name_key",
        "automation_rules",
        ["name"],
    )
    op.drop_index(
        op.f("ix_automation_rules_organization_id"),
        table_name="automation_rules",
    )
    op.drop_constraint(
        op.f("fk_automation_rules_organization_id_organizations"),
        "automation_rules",
        type_="foreignkey",
    )
    op.drop_column("automation_rules", "organization_id")