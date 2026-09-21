"""make conversation organization_id non-nullable

Revision ID: 1f0a0003
Revises: 1f0a0002
Create Date: 2026-09-19 22:10:00.000000

Conversations and conversation messages are always tenant-owned; every
creation path supplies ``organization_id``. Enforcing NOT NULL closes the
defense-in-depth gap where a NULL-org row would be invisible to every
tenant-scoped query while the composite tenant-safe FKs (which perform
``MATCH SIMPLE``) would not be enforced. 1f0a0001 now also creates the
columns NOT NULL; this revision applies the same constraint to databases
that already ran the earlier revision.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1f0a0003'
down_revision: str | Sequence[str] | None = '1f0a0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'conversations',
        'organization_id',
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        'conversation_messages',
        'organization_id',
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'conversations',
        'organization_id',
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        'conversation_messages',
        'organization_id',
        existing_type=sa.Integer(),
        nullable=True,
    )