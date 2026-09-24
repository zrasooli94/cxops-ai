"""add ai evaluation release decisions

Revision ID: 1j0a0003
Revises: 1j0a0002
Create Date: 2026-09-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1j0a0003"
down_revision: str | Sequence[str] | None = "1j0a0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # Composite FK target for release decisions: each baseline row must be
    # addressable by (id, organization_id) exactly like case rows are.
    # ------------------------------------------------------------------
    op.create_unique_constraint(
        "ux_ai_evaluation_baselines_id_organization_id",
        "ai_evaluation_baselines",
        ["id", "organization_id"],
    )

    # ------------------------------------------------------------------
    # AI evaluation release decisions (immutable tenant-owned history)
    # ------------------------------------------------------------------
    op.create_table(
        "ai_evaluation_release_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("candidate_run_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("baseline_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("decided_by_subject", sa.String(length=255), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "comparison_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ai_evaluation_release_decisions_organization_id",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_run_id", "organization_id"],
            ["ai_evaluation_runs.id", "ai_evaluation_runs.organization_id"],
            name="fk_ai_evaluation_release_decisions_candidate_run_organization",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_id", "organization_id"],
            ["ai_evaluation_baselines.id", "ai_evaluation_baselines.organization_id"],
            name="fk_ai_evaluation_release_decisions_baseline_organization",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_evaluation_release_decisions"),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_ai_evaluation_release_decisions_decision",
        ),
    )
    op.create_index(
        "ix_ai_evaluation_release_decisions_organization_id",
        "ai_evaluation_release_decisions",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_evaluation_release_decisions_organization_id_created_at",
        "ai_evaluation_release_decisions",
        ["organization_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_ai_evaluation_release_decisions_organization_id_created_at",
        table_name="ai_evaluation_release_decisions",
    )
    op.drop_index(
        "ix_ai_evaluation_release_decisions_organization_id",
        table_name="ai_evaluation_release_decisions",
    )
    op.drop_table("ai_evaluation_release_decisions")

    op.drop_constraint(
        "ux_ai_evaluation_baselines_id_organization_id",
        "ai_evaluation_baselines",
        type_="unique",
    )