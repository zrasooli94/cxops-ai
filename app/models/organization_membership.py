from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.rbac import OrganizationRole
from app.models.base import Base


class OrganizationMembership(Base):
    """Durable link between an external authenticated identity and a CXOps organization.

    ``subject`` is the stable external user identity (the Nhost JWT ``sub`` claim).
    No tokens are ever stored here. The ``role`` is the authoritative source for
    CXOps authorization inside this organization and is scoped only to this
    membership.
    """

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "subject",
            name="uq_organization_memberships_organization_id_subject",
        ),
        Index("ix_organization_memberships_subject", "subject"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'supervisor', 'agent', 'viewer')",
            name="ck_organization_memberships_role_valid",
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

    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[OrganizationRole] = mapped_column(
        String(20),
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
