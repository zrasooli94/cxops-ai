"""add immutable evaluation live authorization records"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "1q1a0001"
down_revision: str | Sequence[str] | None = "1q0a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_evaluation_live_authorizations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("candidate_run_id", sa.Integer(), nullable=False),
        sa.Column("release_decision_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("authorized_by_subject", sa.String(length=255), nullable=False),
        sa.Column(
            "authorization_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('authorized', 'revoked')",
            name="ck_ai_eval_live_auth_decision",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["candidate_run_id", "organization_id"],
            ["ai_evaluation_runs.id", "ai_evaluation_runs.organization_id"],
            name="fk_ai_eval_live_auth_run_organization",
        ),
        sa.ForeignKeyConstraint(
            ["release_decision_id"], ["ai_evaluation_release_decisions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_eval_live_auth_org_run_created",
        "ai_evaluation_live_authorizations",
        ["organization_id", "candidate_run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_eval_live_auth_org_run_created",
        table_name="ai_evaluation_live_authorizations",
    )
    op.drop_table("ai_evaluation_live_authorizations")
