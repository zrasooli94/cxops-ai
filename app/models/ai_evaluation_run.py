from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AIEvaluationRun(Base):
    """A tenant-owned AI evaluation run.

    ``organization_id`` is the tenant boundary. Every tenant-facing read starts
    from a resolved organization_id and enforces the predicate in SQL.

    ``run_id`` is the stable external identifier used by durable jobs and
    idempotent re-runs. ``metrics`` carries aggregated per-run outputs; raw
    customer message text is never persisted — cases store only derived,
    bounded inputs/results. ``input`` holds the bounded, operator-supplied
    synthetic case definitions needed to re-execute a queued run from the
    worker (no customer body/email/ticket text, no secrets).
    """

    __tablename__ = "ai_evaluation_runs"

    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_ai_evaluation_runs_id_organization_id",
        ),
        UniqueConstraint(
            "run_id",
            name="ux_ai_evaluation_runs_run_id",
        ),
        Index(
            "ix_ai_evaluation_runs_organization_id",
            "organization_id",
        ),
        Index(
            "ix_ai_evaluation_runs_organization_id_created_at",
            "organization_id",
            "created_at",
        ),
        CheckConstraint(
            "target_type IN ('rag', 'agent', 'repeatability', 'latency')",
            name="ck_ai_evaluation_runs_target_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_ai_evaluation_runs_status",
        ),
        CheckConstraint(
            "trigger_source IN ('manual', 'ci', 'scheduler')",
            name="ck_ai_evaluation_runs_trigger_source",
        ),
        CheckConstraint(
            "(pass_rate >= 0 AND pass_rate <= 1) OR pass_rate IS NULL",
            name="ck_ai_evaluation_runs_pass_rate",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    run_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )

    target_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="queued",
        nullable=False,
    )

    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    embedding_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    agent_decision_version: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    tool_policy_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    corpus_revision: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    trigger_source: Mapped[str] = mapped_column(
        String(30),
        default="manual",
        nullable=False,
    )

    requested_by_subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    input: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    pass_rate: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    metrics: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    cases = relationship(
        "AIEvaluationCase",
        back_populates="run",
        cascade="all, delete-orphan",
    )
