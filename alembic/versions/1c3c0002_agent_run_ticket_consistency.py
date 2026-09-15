"""agent run / ticket tenant consistency

Revision ID: 1c3c0002
Revises: 1c3c0001
Create Date: 2026-09-15 00:00:02.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3c0002"
down_revision: str | Sequence[str] | None = "1c3c0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enforce tenant-consistent run/ticket ownership in the database.

    ``organization_id`` is now persisted on ``agent_runs``, and it must never
    drift from the owning ticket's tenant, otherwise an Org A run could drive
    external Zendesk writes against an Org B ticket (the Critical IDOR the
    phase closes). This migration makes the invariant a database constraint:

    - ``UNIQUE (id, organization_id)`` on ``tickets`` — the composite target
      for the run FK (``id`` alone is already the PK, so this is redundant for
      uniqueness and exists solely as the FK target, matching the knowledge
      document/chunk pattern).
    - the run's ticket reference becomes a composite foreign key
      ``(ticket_id, organization_id) → tickets(id, organization_id)`` with
      ``ON DELETE CASCADE``, replacing the single-column FK on ``ticket_id``.

    A mismatched run — ``ticket_id`` referencing an Org B ticket while
    ``organization_id`` is Org A — now violates the composite FK and cannot be
    inserted. Postgres MATCH SIMPLE semantics leave NULL-org legacy runs
    exempt, matching the staged-legacy design: NULL runs remain inert and must
    be migrated explicitly in a later cleanup.
    """
    op.create_unique_constraint(
        op.f("ux_tickets_id_organization_id"),
        "tickets",
        ["id", "organization_id"],
    )

    op.drop_constraint(
        "agent_runs_ticket_id_fkey",
        "agent_runs",
        type_="foreignkey",
    )

    op.create_foreign_key(
        op.f("fk_agent_runs_ticket_organization"),
        "agent_runs",
        "tickets",
        ["ticket_id", "organization_id"],
        ["id", "organization_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Revert to the single-column ticket FK (dropping tenant consistency)."""
    op.drop_constraint(
        op.f("fk_agent_runs_ticket_organization"),
        "agent_runs",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "agent_runs_ticket_id_fkey",
        "agent_runs",
        "tickets",
        ["ticket_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        op.f("ux_tickets_id_organization_id"),
        "tickets",
        type_="unique",
    )