from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentRun(Base):
    """A tenant-owned agent run.

    ``organization_id`` is the tenant boundary. Every tenant-facing read starts
    from a resolved organization_id and enforces the predicate in SQL; NULL-org
    rows are legacy, unmapped runs that remain **inert** — never listed,
    fetched, approved, rejected, executed, or streamed.

    Because the owner is duplicated on the run, drift between run and ticket
    ownership is prevented at the database: the composite foreign key
    ``(ticket_id, organization_id) → tickets(id, organization_id)`` makes it
    impossible to attach a run to a ticket owned by a different tenant. The
    direct ``organization_id`` column is preserved for tenant lookups.
    """

    __tablename__ = "agent_runs"

    __table_args__ = (
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            ondelete="CASCADE",
            name="fk_agent_runs_ticket_organization",
        ),
        Index(
            "ix_agent_runs_organization_id",
            "organization_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    run_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )

    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=True,
    )

    ticket_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    recommended_team: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    recommended_priority: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    response_draft: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    requires_human_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="pending_approval",
        index=True,
        nullable=False,
    )

    sources: Mapped[list[dict]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    workflow_path: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    tool_plan: Mapped[list[dict]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    reviewer_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # --- Phase 1D.3 authorization metadata (nullable, staged) ---
    # These columns are populated during authorization; legacy runs with NULL
    # values must not be silently upgraded and require fresh analysis.
    tool_policy_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    authorization_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    authorization_source: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    authorized_by_subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    authorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
