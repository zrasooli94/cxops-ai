"""add agent run fingerprint

Revision ID: 1e3a0001
Revises: 1e2a0001
Create Date: 2026-09-18 22:46:28.325423

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1e3a0001'
down_revision: str | Sequence[str] | None = '1e2a0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable fingerprint column to agent_runs.

    Legacy rows keep NULL fingerprints; new analyses compute a deterministic
    hash over materially relevant inputs and use it for idempotency.
    """
    op.add_column(
        "agent_runs",
        sa.Column(
            "fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_agent_runs_fingerprint",
        "agent_runs",
        ["fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the fingerprint column and its index."""
    op.drop_index("ix_agent_runs_fingerprint", table_name="agent_runs")
    op.drop_column("agent_runs", "fingerprint")
