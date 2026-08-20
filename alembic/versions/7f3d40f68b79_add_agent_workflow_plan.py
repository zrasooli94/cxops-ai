"""add agent workflow plan

Revision ID: 7f3d40f68b79
Revises: 393e495469c9
Create Date: 2026-08-18 20:03:12.639617
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7f3d40f68b79"

down_revision: str | Sequence[str] | None = "393e495469c9"

branch_labels: str | Sequence[str] | None = None

depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.add_column(
        "agent_runs",
        sa.Column(
            "workflow_path",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    op.add_column(
        "agent_runs",
        sa.Column(
            "tool_plan",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    # Existing rows have now been safely
    # backfilled with [].
    # Remove DB defaults afterward because
    # SQLAlchemy controls defaults for new rows.

    op.alter_column(
        "agent_runs",
        "workflow_path",
        server_default=None,
    )

    op.alter_column(
        "agent_runs",
        "tool_plan",
        server_default=None,
    )


def downgrade() -> None:

    op.drop_column(
        "agent_runs",
        "tool_plan",
    )

    op.drop_column(
        "agent_runs",
        "workflow_path",
    )
