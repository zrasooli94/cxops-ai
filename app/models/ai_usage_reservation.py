from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIUsageReservation(Base):
    """A pre-model-call reservation against a tenant's monthly AI allowance.

    Phase 1P.9R. One row per AI turn, keyed by the turn's idempotency key, so a
    retried or duplicated turn reuses its reservation instead of reserving (or
    counting) twice. ``reserved`` rows hold an *estimated* cost until the turn
    settles; settled turns contribute the costs actually logged in
    ``ai_request_logs`` instead. Both are estimates: provider billing is the
    authority (see docs/runbooks/phase1p9r-release-readiness.md).
    """

    __tablename__ = "ai_usage_reservations"
    __table_args__ = (
        Index("ix_ai_usage_reservations_org_period", "organization_id", "period_start"),
        Index("ix_ai_usage_reservations_period_status", "period_start", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    reservation_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
