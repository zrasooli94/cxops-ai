"""add per-tenant public chat AI allowance (Phase 1P.9)

Additive and nullable: existing rows keep NULL, which means "use the platform
defaults" (settings.public_chat_default_monthly_ai_*). No data is rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1p9a0001"
down_revision: str | Sequence[str] | None = "1p5a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "public_chat_configurations",
        sa.Column("monthly_ai_turn_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "public_chat_configurations",
        sa.Column("monthly_ai_budget_usd", sa.Numeric(10, 2), nullable=True),
    )
    op.create_check_constraint(
        "ck_public_chat_configurations_ai_allowance_non_negative",
        "public_chat_configurations",
        "(monthly_ai_turn_limit IS NULL OR monthly_ai_turn_limit >= 0) AND "
        "(monthly_ai_budget_usd IS NULL OR monthly_ai_budget_usd >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_public_chat_configurations_ai_allowance_non_negative",
        "public_chat_configurations",
        type_="check",
    )
    op.drop_column("public_chat_configurations", "monthly_ai_budget_usd")
    op.drop_column("public_chat_configurations", "monthly_ai_turn_limit")
