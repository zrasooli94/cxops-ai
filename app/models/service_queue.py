from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ServiceQueue(Base):
    """Tenant-owned service queue for intelligent routing and ownership."""

    __tablename__ = "service_queues"

    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_service_queues_id_organization_id",
        ),
        UniqueConstraint(
            "organization_id",
            "key",
            name="ux_service_queues_organization_id_key",
        ),
        UniqueConstraint(
            "organization_id",
            "name",
            name="ux_service_queues_organization_id_name",
        ),
        Index(
            "ix_service_queues_organization_id",
            "organization_id",
        ),
        Index(
            "ix_service_queues_organization_id_is_default",
            "organization_id",
            "is_default",
            unique=True,
            postgresql_where="is_default = true AND active = true",
        ),
        # Tenant-safe SLA policy linkage: a queue may only reference a policy
        # owned by the same organization. MATCH SIMPLE leaves NULLs exempt.
        ForeignKeyConstraint(
            ["sla_policy_id", "organization_id"],
            ["sla_policies.id", "sla_policies.organization_id"],
            name="fk_service_queues_sla_policy_id_organization_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )

    key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    sla_policy_id: Mapped[int | None] = mapped_column(
        nullable=True,
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

    organization = relationship("Organization", back_populates="service_queues")
    sla_policy = relationship(
        "SLAPolicy",
        foreign_keys=[sla_policy_id],
        overlaps="organization,service_queues",
    )
