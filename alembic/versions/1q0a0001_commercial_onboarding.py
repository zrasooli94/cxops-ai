"""commercial onboarding: lifecycle, operators, leads, invitations (Phase 1Q)

Expand-only and compatible with the previous application release (1p9b0001):

* ``organizations.lifecycle_status`` is NOT NULL with a server default of
  ``active``, so every existing organization (including the live pilot) keeps
  its behaviour, and the previous release - which does not know the column -
  keeps inserting organizations unchanged. The other new organization columns
  are nullable.
* ``organization_memberships.email`` is nullable display data.
* ``platform_operators``, ``sales_leads`` and ``organization_invitations`` are
  new tables the previous release never reads.

No existing row is rewritten beyond the column defaults. Downgrade drops only
what this revision adds.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1q0a0001"
down_revision: str | Sequence[str] | None = "1p9b0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "lifecycle_status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column("organizations", sa.Column("website_url", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("plan_code", sa.String(40), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("submitted_for_review_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("lifecycle_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("lifecycle_changed_by", sa.String(255), nullable=True),
    )
    op.create_check_constraint(
        "ck_organizations_lifecycle_status_valid",
        "organizations",
        "lifecycle_status IN ('onboarding', 'pending_review', 'active', 'suspended')",
    )

    op.add_column(
        "organization_memberships",
        sa.Column("email", sa.String(320), nullable=True),
    )

    op.create_table(
        "platform_operators",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("granted_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(255), nullable=True),
    )
    op.create_index(
        "ux_platform_operators_active_subject",
        "platform_operators",
        ["subject"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "sales_leads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("contact_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("industry", sa.String(80), nullable=False),
        sa.Column("inquiry", sa.Text(), nullable=True),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("submission_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "last_submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("operator_notes", sa.Text(), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_by", sa.String(255), nullable=True),
        sa.Column(
            "provisioned_organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('new', 'reviewing', 'contacted', 'approved', 'declined')",
            name="ck_sales_leads_status_valid",
        ),
    )
    op.create_index(
        "ux_sales_leads_open_email",
        "sales_leads",
        ["email_normalized"],
        unique=True,
        postgresql_where=sa.text("status IN ('new', 'reviewing', 'contacted')"),
    )
    op.create_index(
        "ix_sales_leads_status_created",
        "sales_leads",
        ["status", "created_at"],
    )

    op.create_table(
        "organization_invitations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("invited_by_subject", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_subject", sa.String(255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_subject", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked')",
            name="ck_organization_invitations_status_valid",
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'supervisor', 'agent', 'viewer')",
            name="ck_organization_invitations_role_valid",
        ),
    )
    op.create_index(
        "ix_organization_invitations_organization_id",
        "organization_invitations",
        ["organization_id"],
    )
    op.create_index(
        "ux_organization_invitations_token_hash",
        "organization_invitations",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ux_organization_invitations_pending_email",
        "organization_invitations",
        ["organization_id", "email_normalized"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_organization_invitations_pending_email", table_name="organization_invitations"
    )
    op.drop_index(
        "ux_organization_invitations_token_hash", table_name="organization_invitations"
    )
    op.drop_index(
        "ix_organization_invitations_organization_id", table_name="organization_invitations"
    )
    op.drop_table("organization_invitations")
    op.drop_index("ix_sales_leads_status_created", table_name="sales_leads")
    op.drop_index("ux_sales_leads_open_email", table_name="sales_leads")
    op.drop_table("sales_leads")
    op.drop_index("ux_platform_operators_active_subject", table_name="platform_operators")
    op.drop_table("platform_operators")
    op.drop_column("organization_memberships", "email")
    op.drop_constraint(
        "ck_organizations_lifecycle_status_valid", "organizations", type_="check"
    )
    for column in (
        "lifecycle_changed_by",
        "lifecycle_changed_at",
        "submitted_for_review_at",
        "plan_code",
        "website_url",
        "lifecycle_status",
    ):
        op.drop_column("organizations", column)
