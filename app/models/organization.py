from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# Phase 1Q commercial lifecycle. Only ``active`` organizations may spend AI
# allowance, enable their public widget, or reach external business actions.
# ``active`` is the column default so every organization that existed before
# Phase 1Q (and every operator-run manifest) keeps its behaviour; customer-
# facing creation paths set ``onboarding`` explicitly.
LIFECYCLE_ONBOARDING = "onboarding"
LIFECYCLE_PENDING_REVIEW = "pending_review"
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_SUSPENDED = "suspended"
ORGANIZATION_LIFECYCLE_STATUSES = (
    LIFECYCLE_ONBOARDING,
    LIFECYCLE_PENDING_REVIEW,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_SUSPENDED,
)


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status IN ('onboarding', 'pending_review', 'active', 'suspended')",
            name="ck_organizations_lifecycle_status_valid",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
    )

    industry: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )

    external_id: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Phase 1Q: commercial lifecycle and setup progress.
    lifecycle_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=LIFECYCLE_ACTIVE,
        server_default=LIFECYCLE_ACTIVE,
    )
    website_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Internal entitlement label (e.g. "pilot"). Never a price.
    plan_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    submitted_for_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lifecycle_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lifecycle_changed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    customers = relationship(
        "Customer",
        back_populates="organization",
    )

    memberships = relationship(
        "OrganizationMembership",
        back_populates="organization",
    )

    tickets = relationship(
        "Ticket",
        back_populates="organization",
    )

    sla_policies = relationship(
        "SLAPolicy",
        back_populates="organization",
    )

    service_queues = relationship(
        "ServiceQueue",
        back_populates="organization",
    )
