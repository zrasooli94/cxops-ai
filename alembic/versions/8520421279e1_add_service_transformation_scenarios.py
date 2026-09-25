"""add service transformation scenarios

Revision ID: 8520421279e1
Revises: 1j0a0003
Create Date: 2026-09-26 01:11:54.114101

Tenant-owned scenario rows for Phase 1N (Service Transformation Simulation).
organization_id is strictly NOT NULL; scenario rows never carry the legacy
NULL-org semantics of older tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8520421279e1"
down_revision: str | Sequence[str] | None = "1j0a0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "service_transformation_scenarios",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "assumptions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "observed_baseline",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "projected_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("formula_version", sa.String(length=20), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_subject", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_service_transformation_scenarios_organization_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service_transformation_scenarios"),
        sa.CheckConstraint(
            "window_days IN (7, 30, 90)",
            name="ck_service_transformation_scenarios_window_days",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'evaluated', 'archived')",
            name="ck_service_transformation_scenarios_status",
        ),
    )
    op.create_index(
        "ix_service_transformation_scenarios_organization_id",
        "service_transformation_scenarios",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_service_transformation_scenarios_organization_id_created_at",
        "service_transformation_scenarios",
        ["organization_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_service_transformation_scenarios_organization_id_created_at",
        table_name="service_transformation_scenarios",
    )
    op.drop_index(
        "ix_service_transformation_scenarios_organization_id",
        table_name="service_transformation_scenarios",
    )
    op.drop_table("service_transformation_scenarios")