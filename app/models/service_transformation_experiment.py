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

VALID_EXPERIMENT_STATUSES = (
    "draft",
    "ready",
    "running",
    "completed",
    "cancelled",
    "archived",
)
VALID_EXPERIMENT_SCOPE_TYPES = ("organization", "queue")
VALID_EXPERIMENT_WINDOWS = (7, 30, 90)
VALID_EXPERIMENT_MEASUREMENT_STATUSES = (
    "not_measured",
    "measured",
    "insufficient_sample",
    "pricing_unavailable",
    "no_observed_activity",
    "incomplete_window",
)


class ServiceTransformationExperiment(Base):
    """A tenant-owned generic transformation experiment / pilot record.

    An experiment is measurement/control-plane metadata only: it describes the
    change that was intended (``hypothesis``), which scope it applied to
    (``scope_type`` / ``scope_key``), which observed windows were used
    (``baseline_window_days`` / ``measurement_window_days``), what target was
    set (``target_metrics``), and — after ``complete`` — what was actually
    observed (``observed_outcome``) plus the deterministic comparison
    (``outcome_comparison``). It never mutates operational tables and never
    makes a causal claim.

    ``baseline_snapshot`` is captured from live Phase 1L-compatible telemetry,
    never from a client payload, and is immutable after capture (a replacement
    requires a new experiment). ``observed_outcome`` is measured from the same
    live telemetry for the actual run period. ``source_scenario_snapshot`` is a
    bounded read-only copy of a Phase 1N scenario's trusted projected result,
    used only for the projection-variance comparison.
    """

    __tablename__ = "service_transformation_experiments"

    __table_args__ = (
        Index(
            "ix_service_transformation_experiments_organization_id",
            "organization_id",
        ),
        Index(
            "ix_service_transformation_experiments_org_id_created_at",
            "organization_id",
            "created_at",
        ),
        CheckConstraint(
            "status IN ('draft', 'ready', 'running', 'completed', 'cancelled', 'archived')",
            name="ck_service_transformation_experiments_status",
        ),
        CheckConstraint(
            "scope_type IN ('organization', 'queue')",
            name="ck_service_transformation_experiments_scope_type",
        ),
        CheckConstraint(
            "baseline_window_days IN (7, 30, 90)",
            name="ck_service_transformation_experiments_baseline_window_days",
        ),
        CheckConstraint(
            "measurement_window_days IN (7, 30, 90)",
            name="ck_service_transformation_experiments_measurement_window_days",
        ),
        CheckConstraint(
            (
                "measurement_status IN ('not_measured', 'measured', "
                "'insufficient_sample', 'pricing_unavailable', "
                "'no_observed_activity', 'incomplete_window')"
            ),
            name="ck_service_transformation_experiments_measurement_status",
        ),
        CheckConstraint(
            (
                "(scope_type = 'organization' AND scope_key IS NULL) OR "
                "(scope_type = 'queue' AND scope_key IS NOT NULL)"
            ),
            name="ck_service_transformation_experiments_scope_scope_key",
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

    status: Mapped[str] = mapped_column(
        String(20),
        server_default="draft",
        default="draft",
        nullable=False,
    )

    scope_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    scope_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    baseline_window_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    measurement_window_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    planned_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    planned_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    actual_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    actual_ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    hypothesis: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    target_metrics: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    source_scenario_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    source_scenario_snapshot: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    baseline_snapshot: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    baseline_captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    observed_outcome: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    outcome_comparison: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    measured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    measurement_status: Mapped[str | None] = mapped_column(
        String(20),
        server_default="not_measured",
        default="not_measured",
        nullable=True,
    )

    comparison_version: Mapped[str | None] = mapped_column(
        String(20),
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