from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
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


class AIRequestLog(Base):
    """A tenant-owned AI request / usage / telemetry row.

    ``organization_id`` is the tenant boundary. Every observability aggregate
    starts from a resolved organization_id and enforces the predicate in SQL;
    NULL-org rows are legacy, unmapped telemetry that remain **inert** — never
    counted in a tenant's summary, cost, ROI, latency, or drilldown.

    Indexes mirror the observability query shape: ``organization_id`` for the
    group-by / distribution aggregations and ``(organization_id, created_at)``
    for time-window dashboards ordered by ``created_at``.
    """

    __tablename__ = "ai_request_logs"

    __table_args__ = (
        Index(
            "ix_ai_request_logs_organization_id",
            "organization_id",
        ),
        Index(
            "ix_ai_request_logs_organization_id_created_at",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=True,
    )

    request_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )

    feature: Mapped[str] = mapped_column(
        String(100),
        default="rag_answer",
        index=True,
        nullable=False,
    )

    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        index=True,
        nullable=False,
    )

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    answer: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    grounded: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    llm_called: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    retrieval_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    best_similarity: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    input_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    output_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    total_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    estimated_cost_usd: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )

    latency_ms: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    sources: Mapped[list[dict]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
