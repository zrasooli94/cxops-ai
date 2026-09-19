from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Customer(Base):
    __tablename__ = "customers"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "external_id",
            name="ux_customers_organization_id_external_id",
        ),
        UniqueConstraint(
            "organization_id",
            "email",
            name="ux_customers_organization_id_email",
        ),
        # Composite unique that exists solely as the target for the
        # tenant-safe ticket FK (customer_id, organization_id). It is
        # redundant for uniqueness (id is already the PK) but lets the
        # database reject attaching a customer owned by another tenant.
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_customers_id_organization_id",
        ),
        Index(
            "ix_customers_organization_id",
            "organization_id",
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

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    phone: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    organization = relationship(
        "Organization",
        back_populates="customers",
    )

    tickets = relationship(
        "Ticket",
        back_populates="customer",
        foreign_keys="Ticket.customer_id",
    )

    identities = relationship(
        "CustomerIdentity",
        back_populates="customer",
        foreign_keys="CustomerIdentity.customer_id",
    )
