from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Ticket(Base):
    __tablename__ = "tickets"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "external_id",
            name="ux_tickets_organization_id_external_id",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_tickets_id_organization_id",
        ),
        # Tenant-safe customer linkage: a ticket may only reference a customer
        # owned by the same organization. MATCH SIMPLE leaves rows with a NULL
        # customer_id (or NULL organization_id) exempt, so legacy unlinked
        # tickets remain valid while a non-null link is enforced by Postgres.
        # NO ACTION on delete is intentional: there is no customer delete path,
        # and default SET NULL would null organization_id and destroy tenant
        # ownership of the ticket.
        ForeignKeyConstraint(
            ["customer_id", "organization_id"],
            ["customers.id", "customers.organization_id"],
            name="fk_tickets_customer_id_organization_id_customers",
        ),
        # Tenant-safe queue linkage.
        ForeignKeyConstraint(
            ["service_queue_id", "organization_id"],
            ["service_queues.id", "service_queues.organization_id"],
            name="fk_tickets_service_queue_id_organization_id",
        ),
        # Tenant-safe SLA policy linkage.
        ForeignKeyConstraint(
            ["sla_policy_id", "organization_id"],
            ["sla_policies.id", "sla_policies.organization_id"],
            name="fk_tickets_sla_policy_id_organization_id",
        ),
        # Tenant-safe assignee linkage. The membership uniqueness is on
        # (organization_id, subject), so this composite FK enforces that an
        # assigned subject belongs to the ticket's organization.
        ForeignKeyConstraint(
            ["organization_id", "assigned_subject"],
            ["organization_memberships.organization_id", "organization_memberships.subject"],
            name="fk_tickets_organization_id_assigned_subject_memberships",
        ),
        Index(
            "ix_tickets_organization_id",
            "organization_id",
        ),
        Index(
            "ix_tickets_customer_id",
            "customer_id",
        ),
        Index(
            "ix_tickets_organization_id_status",
            "organization_id",
            "status",
        ),
        Index(
            "ix_tickets_organization_id_service_queue_id",
            "organization_id",
            "service_queue_id",
        ),
        Index(
            "ix_tickets_organization_id_assigned_subject",
            "organization_id",
            "assigned_subject",
        ),
        Index(
            "ix_tickets_organization_id_first_response_due_at",
            "organization_id",
            "first_response_due_at",
        ),
        Index(
            "ix_tickets_organization_id_resolution_due_at",
            "organization_id",
            "resolution_due_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    external_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="new",
        nullable=False,
    )

    priority: Mapped[str] = mapped_column(
        String(50),
        default="normal",
        nullable=False,
    )

    requester_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    source: Mapped[str] = mapped_column(
        String(50),
        default="api",
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

    customer_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=True,
    )

    customer = relationship(
        "Customer",
        back_populates="tickets",
        foreign_keys="Ticket.customer_id",
    )

    organization = relationship("Organization", back_populates="tickets")

    service_queue = relationship("ServiceQueue", foreign_keys="Ticket.service_queue_id")
    sla_policy = relationship("SLAPolicy", foreign_keys="Ticket.sla_policy_id")

    category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    assigned_team: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    service_queue_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    assigned_subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    sla_policy_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    first_response_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolution_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    first_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolution_sla_cycle: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    routing_source: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    routed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
