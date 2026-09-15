from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ZendeskOAuthState(Base):
    """Durable OAuth state bound to an organization at initiation time.

    The ``state`` value travelling through the browser is an unguessable random
    nonce: it carries **no** organization, subject, or credential material. The
    authoritative binding (organization_id + initiating subject) lives here.

    Consumption is one-time and atomic: ``consume()`` claims a nonce with an
    UPDATE guarded by ``consumed_at IS NULL`` and ``expires_at > now()``, so a
    replayed or expired callback cannot re-claim a state.
    """

    __tablename__ = "zendesk_oauth_states"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    state: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        index=True,
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
