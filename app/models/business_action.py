from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BusinessAction(Base):
    """A durable business action executed by a business tool (Phase 1P.2).

    One row per executed tool call. ``request_type`` is the fully-qualified
    tool name (e.g. ``a1.create_vehicle_lead``); ``status`` is a bounded
    customer-visible lifecycle (received/processing/needs_review/approved/
    completed/failed); ``reference_id`` is the provider reference returned on
    success; ``payload_json`` holds the validated business arguments (tenant
    internal, never customer-visible) and ``result_json`` the bounded result
    (summary, reference id, safe status). Rows are idempotent by
    ``(organization_id, ticket_id, request_type, dedupe_key)`` where the
    dedupe key derives from the agent run, so a recovered execution converges
    to a single business action.
    """

    __tablename__ = "business_actions"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "ticket_id",
            "request_type",
            "dedupe_key",
            name="ux_business_actions_org_ticket_type_dedupe",
        ),
        # Tenant-safe linkage to the ticket that scopes this action (CASCADE
        # matches the thread lifecycle like agent_runs).
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            name="fk_business_actions_ticket_organization",
            ondelete="CASCADE",
        ),
        # Tenant-safe optional conversation linkage.
        ForeignKeyConstraint(
            ["conversation_id", "organization_id"],
            ["conversations.id", "conversations.organization_id"],
            name="fk_business_actions_conversation_organization",
            ondelete="CASCADE",
        ),
        # Tenant-safe optional customer linkage.
        ForeignKeyConstraint(
            ["customer_id", "organization_id"],
            ["customers.id", "customers.organization_id"],
            name="fk_business_actions_customer_organization",
            ondelete="CASCADE",
        ),
        Index(
            "ix_business_actions_organization_id",
            "organization_id",
        ),
        Index(
            "ix_business_actions_ticket_id",
            "ticket_id",
        ),
        Index(
            "ix_business_actions_reference_id",
            "reference_id",
        ),
        Index(
            "ix_business_actions_run_id",
            "run_id",
        ),
        CheckConstraint(
            "status IN ('received','processing','needs_review','approved','completed','failed')",
            name="ck_business_actions_status",
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

    ticket_id: Mapped[int] = mapped_column(
        nullable=False,
    )

    conversation_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    customer_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    run_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    request_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="received",
        nullable=False,
    )

    reference_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    dedupe_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    payload_json: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    result_json: Mapped[dict] = mapped_column(
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