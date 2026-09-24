from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AIEvaluationCase(Base):
    """A single tenant-owned case result inside an evaluation run.

    ``organization_id`` is the tenant boundary. Because the owner is duplicated
    on the case, drift between case and run ownership is prevented at the
    database: the composite foreign key
    ``(run_id, organization_id) → ai_evaluation_runs(id, organization_id)``
    makes it impossible to attach a case to a run owned by a different tenant.
    The direct ``organization_id`` column is preserved for tenant lookups.

    ``case_id`` is stable within a run so runs can be diffed against baselines;
    ``UNIQUE (run_id, case_id)`` enforces one result per case per run.
    ``input``/``expected``/``actual``/``dimensions`` are JSONB carrying derived
    evaluation data — raw customer message text is never stored.
    """

    __tablename__ = "ai_evaluation_cases"

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "organization_id"],
            ["ai_evaluation_runs.id", "ai_evaluation_runs.organization_id"],
            ondelete="CASCADE",
            name="fk_ai_evaluation_cases_run_organization",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_ai_evaluation_cases_id_organization_id",
        ),
        UniqueConstraint(
            "run_id",
            "case_id",
            name="ux_ai_evaluation_cases_run_id_case_id",
        ),
        Index(
            "ix_ai_evaluation_cases_organization_id",
            "organization_id",
        ),
        Index(
            "ix_ai_evaluation_cases_run_id",
            "run_id",
        ),
        CheckConstraint(
            "case_type IN ('rag', 'agent')",
            name="ck_ai_evaluation_cases_case_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    run_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )

    case_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    case_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    input: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    expected: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    actual: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    dimensions: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    latency_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    total_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    estimated_cost_usd: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    run = relationship(
        "AIEvaluationRun",
        back_populates="cases",
    )
