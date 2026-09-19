from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CustomerIdentity(Base):
    """A first-class provider identity for a tenant-owned customer.

    A customer may have multiple identities from multiple providers/channels.
    The unique boundary is the normalized identifier within a single tenant and
    provider/type combination, so one Zendesk requester id can never map to two
    customers inside the same organization.
    """

    __tablename__ = "customer_identities"

    __table_args__ = (
        ForeignKeyConstraint(
            ["customer_id", "organization_id"],
            ["customers.id", "customers.organization_id"],
            name="fk_customer_identities_customer_organization",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "organization_id",
            "provider",
            "identity_type",
            "normalized_identifier",
            name="ux_customer_identities_tenant_provider_identity",
        ),
        Index(
            "ix_customer_identities_organization_id",
            "organization_id",
        ),
        Index(
            "ix_customer_identities_customer_id",
            "customer_id",
        ),
        Index(
            "ix_customer_identities_provider_lookup",
            "organization_id",
            "provider",
            "identity_type",
            "normalized_identifier",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    organization_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    customer_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    identity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    identifier: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    normalized_identifier: Mapped[str] = mapped_column(
        String(255),
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

    customer = relationship(
        "Customer",
        back_populates="identities",
        foreign_keys=[customer_id],
    )
