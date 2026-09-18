from datetime import datetime

from sqlalchemy import (
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
        Index(
            "ix_tickets_organization_id",
            "organization_id",
        ),
        Index(
            "ix_tickets_customer_id",
            "customer_id",
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

    category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    assigned_team: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
