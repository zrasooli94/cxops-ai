from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIEvaluationBaseline(Base):
    """A tenant-owned immutable AI evaluation baseline snapshot.

    ``organization_id`` is the tenant boundary. Baselines retain history: no
    unique constraint forces a single row per target_type, and a new baseline
    supersedes the old one logically (promotion logic is a later phase) without
    destructively replacing existing baselines.
    """

    __tablename__ = "ai_evaluation_baselines"

    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_ai_evaluation_baselines_id_organization_id",
        ),
        Index(
            "ix_ai_evaluation_baselines_organization_id",
            "organization_id",
        ),
        Index(
            "ix_ai_evaluation_baselines_organization_id_target_type",
            "organization_id",
            "target_type",
        ),
        CheckConstraint(
            "target_type IN ('rag', 'agent', 'repeatability', 'latency')",
            name="ck_ai_evaluation_baselines_target_type",
        ),
        CheckConstraint(
            "(pass_rate >= 0 AND pass_rate <= 1) OR pass_rate IS NULL",
            name="ck_ai_evaluation_baselines_pass_rate",
        ),
        CheckConstraint(
            "cases_count >= 0",
            name="ck_ai_evaluation_baselines_cases_count_non_negative",
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

    target_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    version: Mapped[str] = mapped_column(
        String(30),
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

    pass_rate: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    metrics: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    cases_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    created_by_subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    promoted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
