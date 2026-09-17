"""Add authorization metadata columns to agent_runs (Phase 1D.3).

Revision ID: 1d3a0001
Revises: 1d100001
Create Date: 2026-09-17 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "1d3a0001"
down_revision = "1d100001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_runs",
        sa.Column("tool_policy_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("authorization_digest", sa.String(64), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("authorization_source", sa.String(32), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("authorized_by_subject", sa.String(255), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("agent_runs", "authorized_at")
    op.drop_column("agent_runs", "authorized_by_subject")
    op.drop_column("agent_runs", "authorization_source")
    op.drop_column("agent_runs", "authorization_digest")
    op.drop_column("agent_runs", "tool_policy_version")
