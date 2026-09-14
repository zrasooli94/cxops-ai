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


class OrganizationMembership(Base):
    """Durable link between an external authenticated identity and a CXOps organization.

    ``subject`` is the stable external user identity (the Nhost JWT ``sub`` claim).
    No tokens are ever stored here. Roles/permissions belong to Phase 1D and are
    deliberately absent.
    """

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "subject",
            name="uq_organization_memberships_organization_id_subject",
        ),
        Index("ix_organization_memberships_subject", "subject"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    organization = relationship(
        "Organization",
        back_populates="memberships",
    )