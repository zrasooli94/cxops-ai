"""add grounded auto reply optin"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1p5a0001"
down_revision: str | Sequence[str] | None = "1p4a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "public_chat_configurations",
        sa.Column(
            "grounded_auto_reply_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("public_chat_configurations", "grounded_auto_reply_enabled")
