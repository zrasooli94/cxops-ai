from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIEvaluationLiveAuthorization(Base):
    """Immutable, tenant-scoped authorization for one exact live evaluation."""

    __tablename__ = "ai_evaluation_live_authorizations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_run_id", "organization_id"],
            ["ai_evaluation_runs.id", "ai_evaluation_runs.organization_id"],
            name="fk_ai_eval_live_auth_run_organization",
        ),
        Index(
            "ix_ai_eval_live_auth_org_run_created",
            "organization_id",
            "candidate_run_id",
            "created_at",
        ),
        CheckConstraint(
            "decision IN ('authorized', 'revoked')",
            name="ck_ai_eval_live_auth_decision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    candidate_run_id: Mapped[int] = mapped_column(nullable=False)
    release_decision_id: Mapped[int] = mapped_column(
        ForeignKey("ai_evaluation_release_decisions.id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    authorized_by_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    authorization_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
