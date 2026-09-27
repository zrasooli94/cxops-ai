from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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

# Handoff lifecycle. ``human_assigned`` is reserved for Phase 1P.2 (live-agent
# console); Phase 1P.1 producers are ``ai_active`` -> ``human_requested``
# (widget/agent) and ``closed`` (close endpoint / expiry).
PUBLIC_CHAT_SESSION_STATUSES = frozenset(
    {
        "ai_active",
        "human_requested",
        "human_assigned",
        "closed",
    }
)


class PublicChatConfiguration(Base):
    """Tenant-owned public web-chat configuration.

    The public widget key is never stored in plaintext: only its SHA-256 (hex)
    digest lives here, and the key itself is treated as public (it travels in
    customer-facing HTML). The row is a control-plane record for one embeddable
    widget: display text, an exact-match embedding-origin allowlist, bounded
    comfort/abuse limits, and a short session TTL. A widget key identifies the
    tenant's chat configuration; it grants neither staff access nor tool
    authorization.
    """

    __tablename__ = "public_chat_configurations"

    __table_args__ = (
        UniqueConstraint(
            "public_widget_key_hash",
            name="ux_public_chat_configurations_public_widget_key_hash",
        ),
        # Composite unique that exists solely as the target for the tenant-safe
        # public_chat_session FK (configuration_id, organization_id).
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_public_chat_configurations_id_organization_id",
        ),
        Index(
            "ix_public_chat_configurations_organization_id",
            "organization_id",
        ),
        CheckConstraint(
            "max_message_length BETWEEN 1 AND 10000",
            name="ck_public_chat_configurations_max_message_length",
        ),
        CheckConstraint(
            "max_messages_per_minute BETWEEN 1 AND 300",
            name="ck_public_chat_configurations_max_messages_per_minute",
        ),
        CheckConstraint(
            "session_ttl_hours BETWEEN 1 AND 168",
            name="ck_public_chat_configurations_session_ttl_hours",
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

    public_widget_key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    welcome_message: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    allowed_origins: Mapped[list] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    theme_token: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    max_message_length: Mapped[int] = mapped_column(
        Integer,
        default=4000,
        nullable=False,
    )

    max_messages_per_minute: Mapped[int] = mapped_column(
        Integer,
        default=20,
        nullable=False,
    )

    session_ttl_hours: Mapped[int] = mapped_column(
        Integer,
        default=24,
        nullable=False,
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

    organization = relationship("Organization", viewonly=True)
    sessions = relationship(
        "PublicChatSession",
        back_populates="configuration",
        viewonly=True,
    )


class PublicChatSession(Base):
    """A tenant-owned public chat session (one visitor widget conversation).

    The session carries only a hash of the end-to-end random session token,
    an expiry, the customer-visible handoff status, and tenant-safe links to
    the underlying conversation, ticket, and optional customer. It never stores
    plaintext tokens, PII, IPs, or any authorization material.
    """

    __tablename__ = "public_chat_sessions"

    __table_args__ = (
        UniqueConstraint(
            "token_hash",
            name="ux_public_chat_sessions_token_hash",
        ),
        # Tenant-safe linkage to the configuration (CASCADE: removing a widget
        # config removes its sessions).
        ForeignKeyConstraint(
            ["configuration_id", "organization_id"],
            [
                "public_chat_configurations.id",
                "public_chat_configurations.organization_id",
            ],
            name="fk_public_chat_sessions_configuration_organization",
            ondelete="CASCADE",
        ),
        # Tenant-safe linkage to the conversation (CASCADE matches the thread
        # lifecycle like conversation_messages).
        ForeignKeyConstraint(
            ["conversation_id", "organization_id"],
            ["conversations.id", "conversations.organization_id"],
            name="fk_public_chat_sessions_conversation_organization",
            ondelete="CASCADE",
        ),
        # Tenant-safe linkage to the ticket (CASCADE matches the thread that
        # scopes this chat).
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            name="fk_public_chat_sessions_ticket_organization",
            ondelete="CASCADE",
        ),
        # Tenant-safe optional customer linkage (CASCADE like conversations).
        ForeignKeyConstraint(
            ["customer_id", "organization_id"],
            ["customers.id", "customers.organization_id"],
            name="fk_public_chat_sessions_customer_organization",
            ondelete="CASCADE",
        ),
        Index(
            "ix_public_chat_sessions_organization_id",
            "organization_id",
        ),
        Index(
            "ix_public_chat_sessions_configuration_id",
            "configuration_id",
        ),
        Index(
            "ix_public_chat_sessions_status_created_at",
            "status",
            "created_at",
        ),
        CheckConstraint(
            "status IN ('ai_active', 'human_requested', 'human_assigned', 'closed')",
            name="ck_public_chat_sessions_status",
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

    configuration_id: Mapped[int] = mapped_column(
        nullable=False,
    )

    conversation_id: Mapped[int] = mapped_column(
        nullable=False,
    )

    ticket_id: Mapped[int] = mapped_column(
        nullable=False,
    )

    customer_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="ai_active",
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --- Phase 1P.2 human-handoff assignment (staff) ---
    # Populated by the staff assign/release endpoints; ``human_requested`` ->
    # ``human_assigned`` transitions require a principal subject here.
    assigned_to_subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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

    configuration = relationship(
        "PublicChatConfiguration",
        back_populates="sessions",
        viewonly=True,
    )
    organization = relationship("Organization", viewonly=True)
    conversation = relationship(
        "Conversation",
        primaryjoin=(
            "and_("
            "PublicChatSession.conversation_id == Conversation.id,"
            " PublicChatSession.organization_id == Conversation.organization_id"
            ")"
        ),
        viewonly=True,
    )
    ticket = relationship(
        "Ticket",
        primaryjoin=(
            "and_("
            "PublicChatSession.ticket_id == Ticket.id,"
            " PublicChatSession.organization_id == Ticket.organization_id"
            ")"
        ),
        viewonly=True,
    )
    customer = relationship(
        "Customer",
        primaryjoin=(
            "and_("
            "PublicChatSession.customer_id == Customer.id,"
            " PublicChatSession.organization_id == Customer.organization_id"
            ")"
        ),
        viewonly=True,
    )

    @property
    def is_active(self) -> bool:
        return self.status in {"ai_active", "human_requested", "human_assigned"}

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at

    @property
    def handoff_requested(self) -> bool:
        return self.status in {"human_requested", "human_assigned"}


class PublicChatRateLimitBucket(Base):
    """DB-backed sliding-window counter for public-chat rate limits.

    Phase 1P.2 replaces the process-local in-memory limiter so limits hold
    across instances. One row per (composite key, fixed window bucket); the
    composite key is ``scope + ':' + key`` (e.g. ``session_messages:session:12``).
    A rejected request is not charged (the ``allow`` implementation only
    increments when the count is under the limit), and stale buckets are pruned
    opportunistically. This is a burst guard, not an admission authority.
    """

    __tablename__ = "public_chat_rate_limit_buckets"

    __table_args__ = (
        Index(
            "ix_public_chat_rate_limit_buckets_window_start",
            "window_start",
        ),
    )

    key_cache: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        nullable=False,
    )

    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        nullable=False,
    )

    count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )