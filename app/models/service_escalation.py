from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ServiceEscalation(Base):
    """Current persistent SLA escalation state for one ticket milestone.

    One active materialized escalation per (organization, ticket, milestone).
    Historical transitions are recorded in TicketEvent.
    """

    __tablename__ = "service_escalations"

    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_service_escalations_id_organization_id",
        ),
        UniqueConstraint(
            "organization_id",
            "ticket_id",
            "milestone",
            name="ux_service_escalations_organization_ticket_milestone",
        ),
        UniqueConstraint(
            "event_key",
            name="ux_service_escalations_event_key",
        ),
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            name="fk_service_escalations_ticket_id_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "acknowledged_by_subject"],
            ["organization_memberships.organization_id", "organization_memberships.subject"],
            name="fk_service_escalations_ack_subject_memberships",
        ),
        Index(
            "ix_service_escalations_organization_id",
            "organization_id",
        ),
        Index(
            "ix_service_escalations_organization_id_status",
            "organization_id",
            "status",
        ),
        Index(
            "ix_service_escalations_organization_id_stage",
            "organization_id",
            "stage",
        ),
        CheckConstraint("resolution_sla_cycle >= 0", name="ck_service_escalations_cycle_non_negative"),
        CheckConstraint(
            "milestone IN ('first_response', 'resolution')",
            name="ck_service_escalations_milestone",
        ),
        CheckConstraint(
            "stage IN ('due_soon', 'breached')",
            name="ck_service_escalations_stage",
        ),
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')",
            name="ck_service_escalations_status",
        ),
        CheckConstraint(
            "resolution_reason IN ('milestone_completed', 'escalated_to_breach', 'deadline_recalculated', 'ticket_reopened', 'no_longer_applicable') OR resolution_reason IS NULL",
            name="ck_service_escalations_resolution_reason",
        ),
        CheckConstraint(
            "transition_version >= 1",
            name="ck_service_escalations_transition_version_positive",
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

    milestone: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    stage: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="open",
    )

    # Resolution SLA cycle distinguishes multiple open→resolve→reopen lifecycles.
    resolution_sla_cycle: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    event_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    acknowledged_by_subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolution_reason: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    source: Mapped[str] = mapped_column(
        String(50),
        default="sla_monitor",
        nullable=False,
    )

    transition_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
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

    ticket = relationship("Ticket", foreign_keys=[ticket_id])
