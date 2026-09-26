"""add public chat config and sessions

Revision ID: 1p1a0001
Revises: c3f9a1d2b7e4
Create Date: 2026-09-26 21:30:00.000000

Phase 1P.1 tenant-owned web-chat tables. organization_id is strictly NOT NULL
in both tables. The configuration stores only the SHA-256 hash of the public
widget key (never the plaintext key) plus display/comfort/abuse controls and an
exact-match embedding-origin allowlist. The session stores only the SHA-256 hash
of the end-to-end random session token, an expiry, the customer-visible handoff
status, and tenant-safe links to the conversation/ticket/customer owned by the
same organization. No new rows are written to any pre-existing table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1p1a0001"
down_revision: str | Sequence[str] | None = "c3f9a1d2b7e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "public_chat_configurations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("public_widget_key_hash", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("welcome_message", sa.String(length=500), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "allowed_origins",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("theme_token", sa.String(length=100), nullable=True),
        sa.Column(
            "max_message_length",
            sa.Integer(),
            server_default=sa.text("4000"),
            nullable=False,
        ),
        sa.Column(
            "max_messages_per_minute",
            sa.Integer(),
            server_default=sa.text("20"),
            nullable=False,
        ),
        sa.Column(
            "session_ttl_hours",
            sa.Integer(),
            server_default=sa.text("24"),
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
            name="fk_public_chat_configurations_organization_id",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_public_chat_configurations",
        ),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="ux_public_chat_configurations_id_organization_id",
        ),
        sa.UniqueConstraint(
            "public_widget_key_hash",
            name="ux_public_chat_configurations_public_widget_key_hash",
        ),
        sa.CheckConstraint(
            "max_message_length BETWEEN 1 AND 10000",
            name="ck_public_chat_configurations_max_message_length",
        ),
        sa.CheckConstraint(
            "max_messages_per_minute BETWEEN 1 AND 300",
            name="ck_public_chat_configurations_max_messages_per_minute",
        ),
        sa.CheckConstraint(
            "session_ttl_hours BETWEEN 1 AND 168",
            name="ck_public_chat_configurations_session_ttl_hours",
        ),
    )
    op.create_index(
        "ix_public_chat_configurations_organization_id",
        "public_chat_configurations",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "public_chat_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("configuration_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'ai_active'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_public_chat_sessions_conversation_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id", "organization_id"],
            ["customers.id", "customers.organization_id"],
            name="fk_public_chat_sessions_customer_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            name="fk_public_chat_sessions_ticket_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id", "organization_id"],
            [
                "public_chat_configurations.id",
                "public_chat_configurations.organization_id",
            ],
            name="fk_public_chat_sessions_configuration_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_public_chat_sessions_organization_id",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_public_chat_sessions",
        ),
        sa.UniqueConstraint(
            "token_hash",
            name="ux_public_chat_sessions_token_hash",
        ),
        sa.CheckConstraint(
            "status IN ('ai_active', 'human_requested', 'human_assigned', 'closed')",
            name="ck_public_chat_sessions_status",
        ),
    )
    op.create_index(
        "ix_public_chat_sessions_configuration_id",
        "public_chat_sessions",
        ["configuration_id"],
        unique=False,
    )
    op.create_index(
        "ix_public_chat_sessions_organization_id",
        "public_chat_sessions",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_public_chat_sessions_status_created_at",
        "public_chat_sessions",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_public_chat_sessions_status_created_at",
        table_name="public_chat_sessions",
    )
    op.drop_index(
        "ix_public_chat_sessions_organization_id",
        table_name="public_chat_sessions",
    )
    op.drop_index(
        "ix_public_chat_sessions_configuration_id",
        table_name="public_chat_sessions",
    )
    op.drop_table("public_chat_sessions")
    op.drop_index(
        "ix_public_chat_configurations_organization_id",
        table_name="public_chat_configurations",
    )
    op.drop_table("public_chat_configurations")