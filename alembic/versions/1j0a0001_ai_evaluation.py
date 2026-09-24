"""add ai evaluation tables

Revision ID: 1j0a0001
Revises: 1i0a0001
Create Date: 2026-09-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1j0a0001"
down_revision: str | Sequence[str] | None = "1i0a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # AI evaluation runs
    # ------------------------------------------------------------------
    op.create_table(
        "ai_evaluation_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("agent_decision_version", sa.String(length=30), nullable=True),
        sa.Column("tool_policy_version", sa.Integer(), nullable=True),
        sa.Column("corpus_revision", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trigger_source", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("requested_by_subject", sa.String(length=255), nullable=True),
        sa.Column("pass_rate", sa.Float(), nullable=True),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ai_evaluation_runs_organization_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_evaluation_runs"),
        sa.UniqueConstraint(
            "id", "organization_id", name="ux_ai_evaluation_runs_id_organization_id"
        ),
        sa.UniqueConstraint("run_id", name="ux_ai_evaluation_runs_run_id"),
        sa.CheckConstraint(
            "target_type IN ('rag', 'agent', 'repeatability', 'latency')",
            name="ck_ai_evaluation_runs_target_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_ai_evaluation_runs_status",
        ),
        sa.CheckConstraint(
            "trigger_source IN ('manual', 'ci', 'scheduler')",
            name="ck_ai_evaluation_runs_trigger_source",
        ),
        sa.CheckConstraint(
            "(pass_rate >= 0 AND pass_rate <= 1) OR pass_rate IS NULL",
            name="ck_ai_evaluation_runs_pass_rate",
        ),
    )
    op.create_index(
        "ix_ai_evaluation_runs_organization_id",
        "ai_evaluation_runs",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_evaluation_runs_organization_id_created_at",
        "ai_evaluation_runs",
        ["organization_id", "created_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # AI evaluation case results
    # ------------------------------------------------------------------
    op.create_table(
        "ai_evaluation_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.String(length=100), nullable=False),
        sa.Column("case_type", sa.String(length=30), nullable=False),
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "expected",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "actual",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "dimensions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ai_evaluation_cases_organization_id",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id"],
            ["ai_evaluation_runs.id", "ai_evaluation_runs.organization_id"],
            ondelete="CASCADE",
            name="fk_ai_evaluation_cases_run_organization",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_evaluation_cases"),
        sa.UniqueConstraint(
            "id", "organization_id", name="ux_ai_evaluation_cases_id_organization_id"
        ),
        sa.UniqueConstraint("run_id", "case_id", name="ux_ai_evaluation_cases_run_id_case_id"),
        sa.CheckConstraint(
            "case_type IN ('rag', 'agent')", name="ck_ai_evaluation_cases_case_type"
        ),
    )
    op.create_index(
        "ix_ai_evaluation_cases_organization_id",
        "ai_evaluation_cases",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_evaluation_cases_run_id",
        "ai_evaluation_cases",
        ["run_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # AI evaluation baselines
    # ------------------------------------------------------------------
    op.create_table(
        "ai_evaluation_baselines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=30), nullable=False),
        sa.Column("version", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("agent_decision_version", sa.String(length=30), nullable=True),
        sa.Column("tool_policy_version", sa.Integer(), nullable=True),
        sa.Column("corpus_revision", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pass_rate", sa.Float(), nullable=True),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("cases_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_subject", sa.String(length=255), nullable=True),
        sa.Column("promoted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ai_evaluation_baselines_organization_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_evaluation_baselines"),
        sa.CheckConstraint(
            "target_type IN ('rag', 'agent', 'repeatability', 'latency')",
            name="ck_ai_evaluation_baselines_target_type",
        ),
        sa.CheckConstraint(
            "(pass_rate >= 0 AND pass_rate <= 1) OR pass_rate IS NULL",
            name="ck_ai_evaluation_baselines_pass_rate",
        ),
        sa.CheckConstraint(
            "cases_count >= 0", name="ck_ai_evaluation_baselines_cases_count_non_negative"
        ),
    )
    op.create_index(
        "ix_ai_evaluation_baselines_organization_id",
        "ai_evaluation_baselines",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_evaluation_baselines_organization_id_target_type",
        "ai_evaluation_baselines",
        ["organization_id", "target_type"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_ai_evaluation_baselines_organization_id_target_type",
        table_name="ai_evaluation_baselines",
    )
    op.drop_index(
        "ix_ai_evaluation_baselines_organization_id", table_name="ai_evaluation_baselines"
    )
    op.drop_table("ai_evaluation_baselines")

    op.drop_index("ix_ai_evaluation_cases_run_id", table_name="ai_evaluation_cases")
    op.drop_index("ix_ai_evaluation_cases_organization_id", table_name="ai_evaluation_cases")
    op.drop_table("ai_evaluation_cases")

    op.drop_index(
        "ix_ai_evaluation_runs_organization_id_created_at", table_name="ai_evaluation_runs"
    )
    op.drop_index("ix_ai_evaluation_runs_organization_id", table_name="ai_evaluation_runs")
    op.drop_table("ai_evaluation_runs")
