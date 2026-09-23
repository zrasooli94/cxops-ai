"""add service escalations and resolution sla cycle

Revision ID: 1i0a0001
Revises: 1h0a0001
Create Date: 2026-09-23 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1i0a0001'
down_revision: str | Sequence[str] | None = '1h0a0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # Resolution SLA cycle on tickets
    # ------------------------------------------------------------------
    op.add_column(
        'tickets',
        sa.Column('resolution_sla_cycle', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_check_constraint(
        'ck_tickets_resolution_sla_cycle_non_negative',
        'tickets',
        sa.text('resolution_sla_cycle >= 0'),
    )

    # ------------------------------------------------------------------
    # Service escalations
    # ------------------------------------------------------------------
    op.create_table(
        'service_escalations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('milestone', sa.String(length=30), nullable=False),
        sa.Column('stage', sa.String(length=30), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='open'),
        sa.Column('resolution_sla_cycle', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('event_key', sa.String(length=255), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('triggered_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_by_subject', sa.String(length=255), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_reason', sa.String(length=50), nullable=True),
        sa.Column('source', sa.String(length=50), nullable=False, server_default='sla_monitor'),
        sa.Column('transition_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['organization_id'],
            ['organizations.id'],
            name='fk_service_escalations_organization_id',
        ),
        sa.ForeignKeyConstraint(
            ['ticket_id', 'organization_id'],
            ['tickets.id', 'tickets.organization_id'],
            name='fk_service_escalations_ticket_id_organization_id',
        ),
        sa.ForeignKeyConstraint(
            ['organization_id', 'acknowledged_by_subject'],
            ['organization_memberships.organization_id', 'organization_memberships.subject'],
            name='fk_service_escalations_ack_subject_memberships',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_service_escalations'),
        sa.UniqueConstraint('id', 'organization_id', name='ux_service_escalations_id_organization_id'),
        sa.UniqueConstraint('organization_id', 'ticket_id', 'milestone', name='ux_service_escalations_organization_ticket_milestone'),
        sa.UniqueConstraint('event_key', name='ux_service_escalations_event_key'),
        sa.CheckConstraint("milestone IN ('first_response', 'resolution')", name='ck_service_escalations_milestone'),
        sa.CheckConstraint("stage IN ('due_soon', 'breached')", name='ck_service_escalations_stage'),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name='ck_service_escalations_status'),
        sa.CheckConstraint(
            "resolution_reason IN ('milestone_completed', 'escalated_to_breach', 'deadline_recalculated', 'ticket_reopened', 'no_longer_applicable') OR resolution_reason IS NULL",
            name='ck_service_escalations_resolution_reason',
        ),
        sa.CheckConstraint('resolution_sla_cycle >= 0', name='ck_service_escalations_cycle_non_negative'),
        sa.CheckConstraint('transition_version >= 1', name='ck_service_escalations_transition_version_positive'),
    )
    op.create_index(
        'ix_service_escalations_organization_id',
        'service_escalations',
        ['organization_id'],
        unique=False,
    )
    op.create_index(
        'ix_service_escalations_organization_id_status',
        'service_escalations',
        ['organization_id', 'status'],
        unique=False,
    )
    op.create_index(
        'ix_service_escalations_organization_id_stage',
        'service_escalations',
        ['organization_id', 'stage'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_service_escalations_organization_id_stage', table_name='service_escalations')
    op.drop_index('ix_service_escalations_organization_id_status', table_name='service_escalations')
    op.drop_index('ix_service_escalations_organization_id', table_name='service_escalations')
    op.drop_table('service_escalations')
    op.drop_constraint('ck_tickets_resolution_sla_cycle_non_negative', 'tickets', type_='check')
    op.drop_column('tickets', 'resolution_sla_cycle')
