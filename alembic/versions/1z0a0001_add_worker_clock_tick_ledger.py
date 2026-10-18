"""Add the disabled worker-clock durable slot ledger.

Revision ID: 1z0a0001
Revises: 1q0a0001

The table is additive and contains scheduling metadata only. Creating it does
not enable the receiver, executor, Cloudflare Cron, or any ticket writes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1z0a0001"
down_revision: str | Sequence[str] | None = "1q0a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_clock_ticks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("clock_id", sa.String(length=80), nullable=False),
        sa.Column("slot", sa.BigInteger(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=20), server_default="pending", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'retry', 'failed', 'coalesced')",
            name="ck_worker_clock_ticks_status_valid",
        ),
        sa.CheckConstraint(
            "attempts >= 0", name="ck_worker_clock_ticks_attempts_nonnegative"
        ),
        sa.CheckConstraint(
            "attempts <= max_attempts",
            name="ck_worker_clock_ticks_attempts_within_max",
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 10",
            name="ck_worker_clock_ticks_max_attempts",
        ),
        sa.CheckConstraint("slot > 0", name="ck_worker_clock_ticks_slot_positive"),
        sa.CheckConstraint(
            "length(btrim(clock_id)) > 0",
            name="ck_worker_clock_ticks_clock_id_nonblank",
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND claim_token IS NOT NULL AND locked_at IS NOT NULL) "
            "OR (status <> 'processing' AND claim_token IS NULL AND locked_at IS NULL)",
            name="ck_worker_clock_ticks_claim_consistent",
        ),
        sa.CheckConstraint(
            "(status IN ('completed', 'coalesced') AND completed_at IS NOT NULL) "
            "OR (status NOT IN ('completed', 'coalesced') AND completed_at IS NULL)",
            name="ck_worker_clock_ticks_completion_consistent",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "clock_id", "slot", name="uq_worker_clock_ticks_clock_slot"
        ),
        sa.UniqueConstraint("claim_token", name="uq_worker_clock_ticks_claim_token"),
    )
    op.create_index(
        "ix_worker_clock_ticks_status_slot",
        "worker_clock_ticks",
        ["status", "slot"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_worker_clock_ticks_status_slot", table_name="worker_clock_ticks")
    op.drop_table("worker_clock_ticks")
