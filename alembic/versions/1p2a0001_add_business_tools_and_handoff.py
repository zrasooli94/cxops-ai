"""add business tools and handoff assignment

Revision ID: 1p2a0001
Revises: 1p1a0001
Create Date: 2026-09-27 09:30:00.000000

Phase 1P.2 tenant-owned business-tool tables and staff handoff assignment.

``business_integration_configurations`` is the tenant/provider enablement gate:
a provider (e.g. ``a1_cash_for_cars``) must be enabled here for its tools to be
proposed or executed. ``business_actions`` records one durable, bounded-status
execution per tool call (idempotent by the run-derived dedupe key, tenant-safe
FKs to the owning ticket/conversation/customer). ``public_chat_rate_limit_buckets``
holds DB-backed sliding-window counters so limits hold across instances.
``public_chat_sessions`` gains the staff handoff assignment columns
(``human_requested`` -> ``human_assigned`` requires a principal subject).
No new rows are written to any pre-existing table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1p2a0001"
down_revision: str | Sequence[str] | None = "1p1a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "public_chat_sessions",
        sa.Column(
            "assigned_to_subject",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "public_chat_sessions",
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "public_chat_sessions",
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_table(
        "business_integration_configurations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_business_integration_configurations_organization_id",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_business_integration_configurations",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            name="ux_business_integration_configurations_org_provider",
        ),
        sa.CheckConstraint(
            "provider <> ''",
            name="ck_business_integration_configurations_provider_nonempty",
        ),
    )
    op.create_index(
        "ix_business_integration_configurations_org",
        "business_integration_configurations",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "business_actions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("request_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("reference_id", sa.String(length=64), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "organization_id"],
            ["conversations.id", "conversations.organization_id"],
            name="fk_business_actions_conversation_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id", "organization_id"],
            ["customers.id", "customers.organization_id"],
            name="fk_business_actions_customer_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            name="fk_business_actions_ticket_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_business_actions_organization_id",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_business_actions",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "ticket_id",
            "request_type",
            "dedupe_key",
            name="ux_business_actions_org_ticket_type_dedupe",
        ),
        sa.CheckConstraint(
            "status IN ('received','processing','needs_review','approved','completed','failed')",
            name="ck_business_actions_status",
        ),
    )
    op.create_index(
        "ix_business_actions_organization_id",
        "business_actions",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_business_actions_ticket_id",
        "business_actions",
        ["ticket_id"],
        unique=False,
    )
    op.create_index(
        "ix_business_actions_reference_id",
        "business_actions",
        ["reference_id"],
        unique=False,
    )
    op.create_index(
        "ix_business_actions_run_id",
        "business_actions",
        ["run_id"],
        unique=False,
    )
    op.create_table(
        "public_chat_rate_limit_buckets",
        sa.Column("key_cache", sa.String(length=255), nullable=False),
        sa.Column(
            "window_start",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "key_cache",
            "window_start",
            name="pk_public_chat_rate_limit_buckets",
        ),
    )
    op.create_index(
        "ix_public_chat_rate_limit_buckets_window_start",
        "public_chat_rate_limit_buckets",
        ["window_start"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_public_chat_rate_limit_buckets_window_start",
        table_name="public_chat_rate_limit_buckets",
    )
    op.drop_table("public_chat_rate_limit_buckets")
    op.drop_index(
        "ix_business_actions_run_id",
        table_name="business_actions",
    )
    op.drop_index(
        "ix_business_actions_reference_id",
        table_name="business_actions",
    )
    op.drop_index(
        "ix_business_actions_ticket_id",
        table_name="business_actions",
    )
    op.drop_index(
        "ix_business_actions_organization_id",
        table_name="business_actions",
    )
    op.drop_table("business_actions")
    op.drop_index(
        "ix_business_integration_configurations_org",
        table_name="business_integration_configurations",
    )
    op.drop_table("business_integration_configurations")
    op.drop_column("public_chat_sessions", "released_at")
    op.drop_column("public_chat_sessions", "assigned_at")
    op.drop_column("public_chat_sessions", "assigned_to_subject")