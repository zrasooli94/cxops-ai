from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SLAPolicy(Base):
    """Tenant-owned SLA policy defining first-response and resolution targets.

    Phase 1H uses elapsed wall-clock time (UTC). Business hours, holidays,
    pause-on-pending, and regional calendars are NOT supported yet.
    """

    __tablename__ = "sla_policies"

    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_sla_policies_id_organization_id",
        ),
        UniqueConstraint(
            "organization_id",
            "name",
            name="ux_sla_policies_organization_id_name",
        ),
        Index(
            "ix_sla_policies_organization_id",
            "organization_id",
        ),
        Index(
            "ix_sla_policies_organization_id_is_default",
            "organization_id",
            "is_default",
            unique=True,
            postgresql_where="is_default = true",
        ),
        CheckConstraint("first_response_low_minutes > 0", name="ck_sla_first_response_low_positive"),
        CheckConstraint("first_response_normal_minutes > 0", name="ck_sla_first_response_normal_positive"),
        CheckConstraint("first_response_high_minutes > 0", name="ck_sla_first_response_high_positive"),
        CheckConstraint("first_response_urgent_minutes > 0", name="ck_sla_first_response_urgent_positive"),
        CheckConstraint("resolution_low_minutes > 0", name="ck_sla_resolution_low_positive"),
        CheckConstraint("resolution_normal_minutes > 0", name="ck_sla_resolution_normal_positive"),
        CheckConstraint("resolution_high_minutes > 0", name="ck_sla_resolution_high_positive"),
        CheckConstraint("resolution_urgent_minutes > 0", name="ck_sla_resolution_urgent_positive"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    first_response_low_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    first_response_normal_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    first_response_high_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    first_response_urgent_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    resolution_low_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    resolution_normal_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    resolution_high_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    resolution_urgent_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization = relationship("Organization", back_populates="sla_policies")

    def target_minutes(
        self,
        *,
        metric: str,
        priority: str,
    ) -> int | None:
        """Return the configured target in minutes for a metric/priority pair."""
        normalized = priority.lower().strip() if priority else "normal"
        field = f"{metric}_{normalized}_minutes"
        return getattr(self, field, None)
