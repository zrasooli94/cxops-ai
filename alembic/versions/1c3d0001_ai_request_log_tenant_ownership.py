"""AI request log tenant ownership

Revision ID: 1c3d0001
Revises: 1c3c0002
Create Date: 2026-09-16 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c3d0001"
down_revision: str | Sequence[str] | None = "1c3c0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Stage tenant ownership on AI request logs.

    Adds a nullable ``organization_id`` foreign key so every tenant-owned AI
    request/usage/telemetry row carries its tenant boundary directly. New
    tenant-owned logs always get ``organization_id`` at write time (the record
    API now requires it). Existing rows are backfilled ONLY where ownership is
    deterministic and provable:

      agent ``agent_decision`` logs are identified by ``request_id`` of the
      form ``agent-<run_id>`` (see ``AgentWorkflowService``), so the owning
      organization can be derived from the referenced, tenant-owned
      ``agent_runs`` row exactly when that run is itself non-null-org.

    RAG ``rag_answer`` rows and any other legacy rows have no persisted
    tenant-owned parent, so they are left NULL — they stay **inert** for every
    tenant dashboard (excluded by the ``WHERE organization_id = :org``
    predicate). No arbitrary assignment, no defaulting, no global fallback.
    The eventual ``NOT NULL`` transition is deferred until explicit cleanup.

    Indexes mirror the actual observability query shape: a single-column
    ``organization_id`` index for the distribution/group-by aggregations and a
    composite ``(organization_id, created_at)`` index for time-window
    dashboards that scan a tenant's logs ordered by ``created_at``.
    """

    op.add_column(
        "ai_request_logs",
        sa.Column("organization_id", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        op.f("fk_ai_request_logs_organization_id_organizations"),
        "ai_request_logs",
        "organizations",
        ["organization_id"],
        ["id"],
    )

    op.create_index(
        op.f("ix_ai_request_logs_organization_id"),
        "ai_request_logs",
        ["organization_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_ai_request_logs_organization_id_created_at"),
        "ai_request_logs",
        ["organization_id", "created_at"],
        unique=False,
    )

    # Deterministic, ownership-preserving backfill: an ``agent_decision`` log
    # inherits the organization of the agent run it records exactly when that
    # run is tenant-owned. The ``request_id`` format ``agent-<run_id>`` is the
    # documented, unambiguous link produced by AgentWorkflowService.analyze.
    op.execute(
        """
        UPDATE ai_request_logs
        SET organization_id = agent_runs.organization_id
        FROM agent_runs
        WHERE ai_request_logs.feature = 'agent_decision'
          AND ai_request_logs.request_id = 'agent-' || agent_runs.run_id
          AND agent_runs.organization_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """Drop the staged tenant ownership (and the backfilled values)."""
    op.drop_index(
        op.f("ix_ai_request_logs_organization_id_created_at"),
        table_name="ai_request_logs",
    )

    op.drop_index(
        op.f("ix_ai_request_logs_organization_id"),
        table_name="ai_request_logs",
    )

    op.drop_constraint(
        op.f("fk_ai_request_logs_organization_id_organizations"),
        "ai_request_logs",
        type_="foreignkey",
    )

    op.drop_column("ai_request_logs", "organization_id")