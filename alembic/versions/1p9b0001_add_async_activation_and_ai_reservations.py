"""tenant async activation + AI usage reservations (Phase 1P.9R)

Expand-only and compatible with the previous application release:

* ``public_chat_configurations.async_turns_enabled`` is NOT NULL with a
  server default of ``false``, so code that does not know the column keeps
  inserting configurations unchanged and every tenant starts disabled.
* ``ai_usage_reservations`` is a new table that older code never reads.
* ``ix_ai_request_logs_created_at`` supports the platform-wide month-to-date
  spend check.

No existing row is rewritten. Downgrade drops only what this revision adds.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1p9b0001"
down_revision: str | Sequence[str] | None = "1p9a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "public_chat_configurations",
        sa.Column(
            "async_turns_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "ai_usage_reservations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("reservation_key", sa.String(255), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("reservation_key", name="uq_ai_usage_reservations_key"),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled')",
            name="ck_ai_usage_reservations_status",
        ),
        sa.CheckConstraint(
            "estimated_cost_usd >= 0",
            name="ck_ai_usage_reservations_cost_non_negative",
        ),
    )
    op.create_index(
        "ix_ai_usage_reservations_org_period",
        "ai_usage_reservations",
        ["organization_id", "period_start"],
    )
    op.create_index(
        "ix_ai_usage_reservations_period_status",
        "ai_usage_reservations",
        ["period_start", "status"],
    )
    op.create_index(
        "ix_ai_request_logs_created_at",
        "ai_request_logs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_request_logs_created_at", table_name="ai_request_logs")
    op.drop_index(
        "ix_ai_usage_reservations_period_status", table_name="ai_usage_reservations"
    )
    op.drop_index(
        "ix_ai_usage_reservations_org_period", table_name="ai_usage_reservations"
    )
    op.drop_table("ai_usage_reservations")
    op.drop_column("public_chat_configurations", "async_turns_enabled")
