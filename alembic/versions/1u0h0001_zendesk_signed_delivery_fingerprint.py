"""Add atomic exact-signed-delivery uniqueness for Zendesk webhook jobs.

Nullable and expand-only: historical jobs and all other job types are
unaffected, including writes made by the previous application revision.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1u0h0001"
down_revision: str | Sequence[str] | None = "1q0a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integration_jobs",
        sa.Column("signed_delivery_fingerprint", sa.String(64), nullable=True),
    )
    op.create_index(
        "ux_integration_jobs_signed_delivery_fingerprint",
        "integration_jobs",
        ["signed_delivery_fingerprint"],
        unique=True,
        postgresql_where=sa.text("signed_delivery_fingerprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_integration_jobs_signed_delivery_fingerprint",
        table_name="integration_jobs",
    )
    op.drop_column("integration_jobs", "signed_delivery_fingerprint")
