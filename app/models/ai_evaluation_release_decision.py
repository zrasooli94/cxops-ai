from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIEvaluationReleaseDecision(Base):
    """A tenant-owned immutable human release decision record.

    ``organization_id`` is the tenant boundary. The composite foreign keys
    ``(candidate_run_id, organization_id) → ai_evaluation_runs(id, organization_id)``
    and ``(baseline_id, organization_id) → ai_evaluation_baselines(id, organization_id)``
    make it impossible to record a decision whose candidate run or baseline is
    owned by a different tenant; cross-tenant references fail at the database.

    History is immutable: there is no update or delete path here. A later
    decision for the same candidate simply inserts a new row. ``comparison_snapshot``
    stores the safe output of ``compare_run_to_baseline`` at decision time (pass
    rates, deltas, metric comparisons, version identity) — never raw run input,
    customer/ticket/prompt text, or secrets.
    """

    __tablename__ = "ai_evaluation_release_decisions"

    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_run_id", "organization_id"],
            ["ai_evaluation_runs.id", "ai_evaluation_runs.organization_id"],
            name="fk_ai_evaluation_release_decisions_candidate_run_organization",
        ),
        ForeignKeyConstraint(
            ["baseline_id", "organization_id"],
            ["ai_evaluation_baselines.id", "ai_evaluation_baselines.organization_id"],
            name="fk_ai_evaluation_release_decisions_baseline_organization",
        ),
        Index(
            "ix_ai_evaluation_release_decisions_organization_id",
            "organization_id",
        ),
        Index(
            "ix_ai_evaluation_release_decisions_organization_id_created_at",
            "organization_id",
            "created_at",
        ),
        CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_ai_evaluation_release_decisions_decision",
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

    candidate_run_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    baseline_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    decision: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    decided_by_subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    note: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    comparison_snapshot: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )