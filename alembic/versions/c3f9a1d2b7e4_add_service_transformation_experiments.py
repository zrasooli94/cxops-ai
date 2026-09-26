"""add service transformation experiments

Revision ID: c3f9a1d2b7e4
Revises: 8520421279e1
Create Date: 2026-09-26 06:20:00.000000

Tenant-owned experiment rows for Phase 1O (Transformation Experimentation &
Outcome Validation). organization_id is strictly NOT NULL; experiment rows never
carry the legacy NULL-org semantics of older tables. The table stores only
measurement/control-plane metadata: a bounded source-scenario snapshot, an
immutable baseline snapshot, the observed-outcome snapshot, and the persisted
deterministic comparison (including its limitations), so detail views never have
to reconstruct execution-time warnings.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f9a1d2b7e4"
down_revision: str | Sequence[str] | None = "8520421279e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "service_transformation_experiments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_key", sa.String(length=100), nullable=True),
        sa.Column("baseline_window_days", sa.Integer(), nullable=False),
        sa.Column("measurement_window_days", sa.Integer(), nullable=False),
        sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "hypothesis",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "target_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("source_scenario_id", sa.Integer(), nullable=True),
        sa.Column(
            "source_scenario_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "baseline_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("baseline_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "observed_outcome",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "outcome_comparison",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "measurement_status",
            sa.String(length=20),
            server_default=sa.text("'not_measured'"),
            nullable=True,
        ),
        sa.Column("comparison_version", sa.String(length=20), nullable=True),
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
            name="fk_service_transformation_experiments_organization_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service_transformation_experiments"),
        sa.CheckConstraint(
            (
                "status IN ('draft', 'ready', 'running', 'completed', "
                "'cancelled', 'archived')"
            ),
            name="ck_service_transformation_experiments_status",
        ),
        sa.CheckConstraint(
            "scope_type IN ('organization', 'queue')",
            name="ck_service_transformation_experiments_scope_type",
        ),
        sa.CheckConstraint(
            "baseline_window_days IN (7, 30, 90)",
            name="ck_service_transformation_experiments_baseline_window_days",
        ),
        sa.CheckConstraint(
            "measurement_window_days IN (7, 30, 90)",
            name="ck_service_transformation_experiments_measurement_window_days",
        ),
        sa.CheckConstraint(
            (
                "measurement_status IN ('not_measured', 'measured', "
                "'insufficient_sample', 'pricing_unavailable', "
                "'no_observed_activity', 'incomplete_window')"
            ),
            name="ck_service_transformation_experiments_measurement_status",
        ),
        sa.CheckConstraint(
            (
                "(scope_type = 'organization' AND scope_key IS NULL) OR "
                "(scope_type = 'queue' AND scope_key IS NOT NULL)"
            ),
            name="ck_service_transformation_experiments_scope_scope_key",
        ),
    )
    op.create_index(
        "ix_service_transformation_experiments_organization_id",
        "service_transformation_experiments",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_service_transformation_experiments_org_id_created_at",
        "service_transformation_experiments",
        ["organization_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_service_transformation_experiments_org_id_created_at",
        table_name="service_transformation_experiments",
    )
    op.drop_index(
        "ix_service_transformation_experiments_organization_id",
        table_name="service_transformation_experiments",
    )
    op.drop_table("service_transformation_experiments")