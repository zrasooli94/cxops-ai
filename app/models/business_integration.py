from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BusinessIntegrationConfiguration(Base):
    """Tenant-owned enablement of a business-tool provider (Phase 1P.2).

    One row per (organization, provider). A provider must be enabled here for
    its tools to be proposed by the agent or executed by the worker — this is
    the tenant/provider tool-scoping gate, so providers a tenant never
    subscribed to (e.g. RISPU) can never see a tool namespace. ``config_json``
    is bounded, tenant-internal configuration for the provider adapter.
    """

    __tablename__ = "business_integration_configurations"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider",
            name="ux_business_integration_configurations_org_provider",
        ),
        Index(
            "ix_business_integration_configurations_org",
            "organization_id",
        ),
        CheckConstraint(
            "provider <> ''",
            name="ck_business_integration_configurations_provider_nonempty",
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

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    config_json: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
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