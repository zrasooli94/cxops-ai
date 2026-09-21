"""add human reply delivery lifecycle to conversation messages

Revision ID: 1g0a0001
Revises: 1f0a0003
Create Date: 2026-09-21 22:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1g0a0001'
down_revision: str | Sequence[str] | None = '1f0a0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'conversation_messages',
        sa.Column('delivery_status', sa.String(length=30), nullable=True)
    )
    op.add_column(
        'conversation_messages',
        sa.Column('delivery_token', sa.String(length=255), nullable=True)
    )
    op.add_column(
        'conversation_messages',
        sa.Column('requested_by_subject', sa.String(length=255), nullable=True)
    )
    op.add_column(
        'conversation_messages',
        sa.Column('delivery_error_code', sa.String(length=50), nullable=True)
    )
    op.add_column(
        'conversation_messages',
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True)
    )

    # Bounded delivery-state lookups for the inbox and worker.
    op.create_index(
        'ix_conv_msg_org_conv_delivery_status',
        'conversation_messages',
        ['organization_id', 'conversation_id', 'delivery_status'],
        unique=False,
    )

    # Delivery token is server-generated and used for Zendesk crash-recovery
    # marker reconciliation. It is unique within a tenant when present; NULLs
    # are allowed for provider-ingested messages and agent mirrors.
    op.create_index(
        'ux_conversation_messages_delivery_token',
        'conversation_messages',
        ['organization_id', 'delivery_token'],
        unique=True,
        postgresql_where=sa.text('delivery_token IS NOT NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ux_conversation_messages_delivery_token',
        table_name='conversation_messages',
    )
    op.drop_index(
        'ix_conv_msg_org_conv_delivery_status',
        table_name='conversation_messages',
    )
    op.drop_column('conversation_messages', 'delivered_at')
    op.drop_column('conversation_messages', 'delivery_error_code')
    op.drop_column('conversation_messages', 'requested_by_subject')
    op.drop_column('conversation_messages', 'delivery_token')
    op.drop_column('conversation_messages', 'delivery_status')
