from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

VALID_SCENARIO_STATUSES = ("draft", "evaluated", "archived")
VALID_SCENARIO_WINDOWS = (7, 30, 90)


class ServiceTransformationScenario(Base):
    """A tenant-owned what-if scenario over the Phase 1L transformation
    analytics.

    ``organization_id`` is the tenant boundary and is strictly NOT NULL —
    scenario rows never carry the legacy NULL-org semantics of older tables.

    ``assumptions`` holds the operator-supplied, strictly validated 0–100
    percentage targets (see SimulationAssumptions). ``observed_baseline`` is
    the bounded, read-only snapshot captured from the Phase 1L
    ServiceTransformationService at evaluation time, and ``projected_result``
    is the deterministic engine output for that baseline + assumptions. Raw
    customer message text and employee-level performance data are never
    persisted. Evaluation never mutates operational tables: this row is the
    only write performed by the simulation flow.
    """

    __tablename__ = "service_transformation_scenarios"

    __table_args__ = (
        Index(
            "ix_service_transformation_scenarios_organization_id",
            "organization_id",
        ),
        Index(
            "ix_service_transformation_scenarios_organization_id_created_at",
            "organization_id",
            "created_at",
        ),
        CheckConstraint(
            "window_days IN (7, 30, 90)",
            name="ck_service_transformation_scenarios_window_days",
        ),
        CheckConstraint(
            "status IN ('draft', 'evaluated', 'archived')",
            name="ck_service_transformation_scenarios_status",
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

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    window_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        server_default="draft",
        default="draft",
        nullable=False,
    )

    assumptions: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    observed_baseline: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    projected_result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    formula_version: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by_subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
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