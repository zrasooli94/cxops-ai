"""Phase 1Q commercial onboarding models.

* ``PlatformOperator`` - the only source of platform-wide (cross-tenant)
  authority. A row is keyed by the verified Nhost subject and is granted only
  through ``scripts/manage_platform_operators.py`` (database access), never by
  an HTTP endpoint, an email address, or a JWT claim. Owning a tenant confers
  no platform authority.
* ``SalesLead`` - an early-access / demo request. A lead is *interest*, not a
  customer: it never creates an organization or membership by itself.
* ``OrganizationInvitation`` - a single-use, expiring team invitation. Only the
  SHA-256 digest of the token is stored.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

LEAD_STATUS_NEW = "new"
LEAD_STATUS_REVIEWING = "reviewing"
LEAD_STATUS_CONTACTED = "contacted"
LEAD_STATUS_APPROVED = "approved"
LEAD_STATUS_DECLINED = "declined"
LEAD_STATUSES = (
    LEAD_STATUS_NEW,
    LEAD_STATUS_REVIEWING,
    LEAD_STATUS_CONTACTED,
    LEAD_STATUS_APPROVED,
    LEAD_STATUS_DECLINED,
)
# Leads still being worked. At most one open lead exists per email address, so
# a repeat submission folds into the lead an operator is already handling.
LEAD_OPEN_STATUSES = (LEAD_STATUS_NEW, LEAD_STATUS_REVIEWING, LEAD_STATUS_CONTACTED)

INVITATION_STATUS_PENDING = "pending"
INVITATION_STATUS_ACCEPTED = "accepted"
INVITATION_STATUS_REVOKED = "revoked"
INVITATION_STATUSES = (
    INVITATION_STATUS_PENDING,
    INVITATION_STATUS_ACCEPTED,
    INVITATION_STATUS_REVOKED,
)


class PlatformOperator(Base):
    __tablename__ = "platform_operators"
    __table_args__ = (
        # One active grant per subject; revoked grants are kept for audit.
        Index(
            "ux_platform_operators_active_subject",
            "subject",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SalesLead(Base):
    __tablename__ = "sales_leads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'reviewing', 'contacted', 'approved', 'declined')",
            name="ck_sales_leads_status_valid",
        ),
        Index(
            "ux_sales_leads_open_email",
            "email_normalized",
            unique=True,
            postgresql_where=text("status IN ('new', 'reviewing', 'contacted')"),
        ),
        Index("ix_sales_leads_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str] = mapped_column(String(80), nullable=False)
    inquiry: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LEAD_STATUS_NEW, server_default=LEAD_STATUS_NEW
    )
    submission_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    last_submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    operator_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_changed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provisioned_organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OrganizationInvitation(Base):
    __tablename__ = "organization_invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked')",
            name="ck_organization_invitations_status_valid",
        ),
        CheckConstraint(
            "role IN ('owner', 'admin', 'supervisor', 'agent', 'viewer')",
            name="ck_organization_invitations_role_valid",
        ),
        Index("ux_organization_invitations_token_hash", "token_hash", unique=True),
        # One live invitation per address per organization; re-inviting
        # revokes the previous one in the same transaction.
        Index(
            "ux_organization_invitations_pending_email",
            "organization_id",
            "email_normalized",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=INVITATION_STATUS_PENDING,
        server_default=INVITATION_STATUS_PENDING,
    )
    invited_by_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
