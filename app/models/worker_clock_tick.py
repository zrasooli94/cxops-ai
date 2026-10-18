from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkerClockTick(Base):
    """Durable, non-tenant scheduling intent; it contains no customer data."""

    __tablename__ = "worker_clock_ticks"
    __table_args__ = (
        UniqueConstraint("clock_id", "slot", name="uq_worker_clock_ticks_clock_slot"),
        UniqueConstraint("claim_token", name="uq_worker_clock_ticks_claim_token"),
        Index("ix_worker_clock_ticks_status_slot", "status", "slot"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'retry', 'failed', 'coalesced')",
            name="ck_worker_clock_ticks_status_valid",
        ),
        CheckConstraint(
            "attempts >= 0", name="ck_worker_clock_ticks_attempts_nonnegative"
        ),
        CheckConstraint(
            "attempts <= max_attempts",
            name="ck_worker_clock_ticks_attempts_within_max",
        ),
        CheckConstraint(
            "max_attempts BETWEEN 1 AND 10", name="ck_worker_clock_ticks_max_attempts"
        ),
        CheckConstraint("slot > 0", name="ck_worker_clock_ticks_slot_positive"),
        CheckConstraint(
            "length(btrim(clock_id)) > 0",
            name="ck_worker_clock_ticks_clock_id_nonblank",
        ),
        CheckConstraint(
            "(status = 'processing' AND claim_token IS NOT NULL AND locked_at IS NOT NULL) "
            "OR (status <> 'processing' AND claim_token IS NULL AND locked_at IS NULL)",
            name="ck_worker_clock_ticks_claim_consistent",
        ),
        CheckConstraint(
            "(status IN ('completed', 'coalesced') AND completed_at IS NOT NULL) "
            "OR (status NOT IN ('completed', 'coalesced') AND completed_at IS NULL)",
            name="ck_worker_clock_ticks_completion_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    clock_id: Mapped[str] = mapped_column(String(80), nullable=False)
    slot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), server_default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(
        Integer, server_default="3", nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(36))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
