from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ZendeskOAuthToken(Base):
    """Organization-owned Zendesk integration credential.

    One active connection per organization. ``organization_id`` is the tenant
    boundary: every credential lookup in tenant-facing code starts from a
    resolved organization_id and never falls back to a global row.

    A row with ``organization_id`` NULL is a legacy, unresolved global token
    (pre-1C.3A). It is deliberately **inactive**: no code path resolves it, it
    is never refreshed, and it is never used to authenticate Zendesk calls.
    """

    __tablename__ = "zendesk_oauth_tokens"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            name="ux_zendesk_oauth_tokens_organization_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    integration_id: Mapped[str | None] = mapped_column(
        String(36),
        unique=True,
        index=True,
        nullable=True,
    )

    access_token: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    refresh_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    token_type: Mapped[str] = mapped_column(
        String(50),
        default="bearer",
        nullable=False,
    )

    scope: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    webhook_secret: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    connected_by: Mapped[str | None] = mapped_column(
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
