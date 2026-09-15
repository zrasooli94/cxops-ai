"""agent run tenant isolation

Revision ID: 1c3c0001
Revises: 1c3b0002
Create Date: 2026-09-15 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3c0001"
down_revision: str | Sequence[str] | None = "1c3b0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Stage tenant ownership on agent runs.

    Adds a nullable ``organization_id`` foreign key to ``agent_runs`` so a run
    carries its tenant boundary directly (mirroring the knowledge document /
    chunk pattern). Existing runs are backfilled ONLY when the owning ticket's
    ``organization_id`` is non-null — ownership is unambiguous because each run
    points at exactly one ticket row via the primary key, and the ticket's org
    is a single persisted value. Runs whose ticket is itself unowned (legacy
    NULL-org ticket) are left NULL; they stay **inert** for every tenant-facing
    surface.

    The eventual ``NOT NULL`` transition is deferred until explicit cleanup of
    the NULL-org legacy rows, per the staged-legacy design.
    """
    op.add_column(
        "agent_runs",
        sa.Column("organization_id", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        op.f("fk_agent_runs_organization_id_organizations"),
        "agent_runs",
        "organizations",
        ["organization_id"],
        ["id"],
    )

    op.create_index(
        op.f("ix_agent_runs_organization_id"),
        "agent_runs",
        ["organization_id"],
        unique=False,
    )

    # Deterministic, ownership-preserving backfill: a run inherits the
    # organization of its ticket exactly when that ticket is tenant-owned.
    # No arbitrary assignment, no defaulting, no fallback.
    op.execute(
        """
        UPDATE agent_runs
        SET organization_id = tickets.organization_id
        FROM tickets
        WHERE agent_runs.ticket_id = tickets.id
          AND tickets.organization_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """Drop the staged tenant ownership (and the backfilled values)."""
    op.drop_index(
        op.f("ix_agent_runs_organization_id"),
        table_name="agent_runs",
    )

    op.drop_constraint(
        op.f("fk_agent_runs_organization_id_organizations"),
        "agent_runs",
        type_="foreignkey",
    )

    op.drop_column("agent_runs", "organization_id")