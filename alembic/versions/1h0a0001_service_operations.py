"""add service operations: sla policies, service queues, and ticket routing

Revision ID: 1h0a0001
Revises: 1g0a0001
Create Date: 2026-09-22 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1h0a0001'
down_revision: str | Sequence[str] | None = '1g0a0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # SLA policies
    # ------------------------------------------------------------------
    op.create_table(
        'sla_policies',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('first_response_low_minutes', sa.Integer(), nullable=False),
        sa.Column('first_response_normal_minutes', sa.Integer(), nullable=False),
        sa.Column('first_response_high_minutes', sa.Integer(), nullable=False),
        sa.Column('first_response_urgent_minutes', sa.Integer(), nullable=False),
        sa.Column('resolution_low_minutes', sa.Integer(), nullable=False),
        sa.Column('resolution_normal_minutes', sa.Integer(), nullable=False),
        sa.Column('resolution_high_minutes', sa.Integer(), nullable=False),
        sa.Column('resolution_urgent_minutes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_sla_policies_organization_id'),
        sa.PrimaryKeyConstraint('id', name='pk_sla_policies'),
        sa.UniqueConstraint('id', 'organization_id', name='ux_sla_policies_id_organization_id'),
        sa.UniqueConstraint('organization_id', 'name', name='ux_sla_policies_organization_id_name'),
        sa.CheckConstraint('first_response_low_minutes > 0', name='ck_sla_first_response_low_positive'),
        sa.CheckConstraint('first_response_normal_minutes > 0', name='ck_sla_first_response_normal_positive'),
        sa.CheckConstraint('first_response_high_minutes > 0', name='ck_sla_first_response_high_positive'),
        sa.CheckConstraint('first_response_urgent_minutes > 0', name='ck_sla_first_response_urgent_positive'),
        sa.CheckConstraint('resolution_low_minutes > 0', name='ck_sla_resolution_low_positive'),
        sa.CheckConstraint('resolution_normal_minutes > 0', name='ck_sla_resolution_normal_positive'),
        sa.CheckConstraint('resolution_high_minutes > 0', name='ck_sla_resolution_high_positive'),
        sa.CheckConstraint('resolution_urgent_minutes > 0', name='ck_sla_resolution_urgent_positive'),
    )
    op.create_index(
        'ix_sla_policies_organization_id',
        'sla_policies',
        ['organization_id'],
        unique=False,
    )
    op.create_index(
        'ix_sla_policies_organization_id_is_default',
        'sla_policies',
        ['organization_id', 'is_default'],
        unique=True,
        postgresql_where=sa.text('is_default = true'),
    )

    # ------------------------------------------------------------------
    # Service queues
    # ------------------------------------------------------------------
    op.create_table(
        'service_queues',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('sla_policy_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name='fk_service_queues_organization_id'),
        sa.ForeignKeyConstraint(
            ['sla_policy_id', 'organization_id'],
            ['sla_policies.id', 'sla_policies.organization_id'],
            name='fk_service_queues_sla_policy_id_organization_id',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_service_queues'),
        sa.UniqueConstraint('id', 'organization_id', name='ux_service_queues_id_organization_id'),
        sa.UniqueConstraint('organization_id', 'key', name='ux_service_queues_organization_id_key'),
        sa.UniqueConstraint('organization_id', 'name', name='ux_service_queues_organization_id_name'),
    )
    op.create_index(
        'ix_service_queues_organization_id',
        'service_queues',
        ['organization_id'],
        unique=False,
    )
    op.create_index(
        'ix_service_queues_organization_id_is_default',
        'service_queues',
        ['organization_id', 'is_default'],
        unique=True,
        postgresql_where=sa.text('is_default = true AND active = true'),
    )

    # ------------------------------------------------------------------
    # Ticket service-operations fields
    # ------------------------------------------------------------------
    op.add_column('tickets', sa.Column('service_queue_id', sa.Integer(), nullable=True))
    op.add_column('tickets', sa.Column('assigned_subject', sa.String(length=255), nullable=True))
    op.add_column('tickets', sa.Column('sla_policy_id', sa.Integer(), nullable=True))
    op.add_column('tickets', sa.Column('first_response_due_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tickets', sa.Column('resolution_due_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tickets', sa.Column('first_response_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tickets', sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tickets', sa.Column('routing_source', sa.String(length=50), nullable=True))
    op.add_column('tickets', sa.Column('routed_at', sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key(
        'fk_tickets_service_queue_id_organization_id',
        'tickets',
        'service_queues',
        ['service_queue_id', 'organization_id'],
        ['id', 'organization_id'],
    )
    op.create_foreign_key(
        'fk_tickets_sla_policy_id_organization_id',
        'tickets',
        'sla_policies',
        ['sla_policy_id', 'organization_id'],
        ['id', 'organization_id'],
    )
    op.create_foreign_key(
        'fk_tickets_organization_id_assigned_subject_memberships',
        'tickets',
        'organization_memberships',
        ['organization_id', 'assigned_subject'],
        ['organization_id', 'subject'],
    )

    op.create_index('ix_tickets_organization_id_status', 'tickets', ['organization_id', 'status'], unique=False)
    op.create_index('ix_tickets_organization_id_service_queue_id', 'tickets', ['organization_id', 'service_queue_id'], unique=False)
    op.create_index('ix_tickets_organization_id_assigned_subject', 'tickets', ['organization_id', 'assigned_subject'], unique=False)
    op.create_index('ix_tickets_organization_id_first_response_due_at', 'tickets', ['organization_id', 'first_response_due_at'], unique=False)
    op.create_index('ix_tickets_organization_id_resolution_due_at', 'tickets', ['organization_id', 'resolution_due_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_tickets_organization_id_resolution_due_at', table_name='tickets')
    op.drop_index('ix_tickets_organization_id_first_response_due_at', table_name='tickets')
    op.drop_index('ix_tickets_organization_id_assigned_subject', table_name='tickets')
    op.drop_index('ix_tickets_organization_id_service_queue_id', table_name='tickets')
    op.drop_index('ix_tickets_organization_id_status', table_name='tickets')

    op.drop_constraint('fk_tickets_organization_id_assigned_subject_memberships', 'tickets', type_='foreignkey')
    op.drop_constraint('fk_tickets_sla_policy_id_organization_id', 'tickets', type_='foreignkey')
    op.drop_constraint('fk_tickets_service_queue_id_organization_id', 'tickets', type_='foreignkey')

    op.drop_column('tickets', 'routed_at')
    op.drop_column('tickets', 'routing_source')
    op.drop_column('tickets', 'resolved_at')
    op.drop_column('tickets', 'first_response_at')
    op.drop_column('tickets', 'resolution_due_at')
    op.drop_column('tickets', 'first_response_due_at')
    op.drop_column('tickets', 'sla_policy_id')
    op.drop_column('tickets', 'assigned_subject')
    op.drop_column('tickets', 'service_queue_id')

    op.drop_index('ix_service_queues_organization_id_is_default', table_name='service_queues')
    op.drop_index('ix_service_queues_organization_id', table_name='service_queues')
    op.drop_table('service_queues')

    op.drop_index('ix_sla_policies_organization_id_is_default', table_name='sla_policies')
    op.drop_index('ix_sla_policies_organization_id', table_name='sla_policies')
    op.drop_table('sla_policies')
