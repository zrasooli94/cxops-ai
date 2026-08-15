from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    event_key: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        index=True,
        nullable=False,
    )

    source: Mapped[str] = mapped_column(
        String(50),
        default="internal",
        nullable=False,
    )

    ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"),
        nullable=True,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    processed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ticket = relationship("Ticket")