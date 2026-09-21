"""cascade conversation fks (retained no-op)

Revision ID: 1f0a0002
Revises: 1f0a0001
Create Date: 2026-09-19 21:39:30.390182

The conversations/comments customer and ticket composite foreign keys are now
created with ``ondelete='CASCADE'`` directly in 1f0a0001 (matching the models).
This revision is retained in history for databases where 1f0a0002 already ran:
upgrade/downgrade are no-ops because the target schema — CASCADE on
``fk_conversations_customer_organization_customers`` and
``fk_conversations_ticket_organization_tickets`` — is already in force on both
fresh (1f0a0001) and already-upgraded databases.

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '1f0a0002'
down_revision: str | Sequence[str] | None = '1f0a0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema (no-op: cascade already in force)."""


def downgrade() -> None:
    """Downgrade schema (no-op)."""