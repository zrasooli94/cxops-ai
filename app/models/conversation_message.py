from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ConversationMessage(Base):
    """A single message inside a conversation thread.

    Every message is tenant-owned and deduplicated two ways: by the provider's
    own external message id and by a deterministic ``dedupe_key`` that callers
    control (e.g. ``ticket_initial:<ticket-id>`` or
    ``agent_run:<run-id>:zendesk.send_reply``). At least one of the two is set
    for provider-backed messages so re-delivery is impossible. ``direction``
    (inbound/outbound/internal) and ``visibility`` (public/internal) are
    bounded values; no raw provider payload is stored.

    Human-submitted outbound replies additionally carry a delivery lifecycle:
    ``delivery_status`` tracks queued/sending/retrying/sent/failed,
    ``delivery_token`` is the server-generated crash-recovery marker,
    ``requested_by_subject`` is the authenticated human author, and
    ``delivery_error_code`` holds a safe bounded failure category.
    """

    __tablename__ = "conversation_messages"

    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_conversation_messages_id_organization_id",
        ),
        # Provider-side dedupe: the same external message can never be ingested
        # twice, including across tenants (unlikely but cheap to keep safe).
        UniqueConstraint(
            "organization_id",
            "provider",
            "external_message_id",
            name="ux_conversation_messages_tenant_provider_external",
        ),
        # Local deterministic dedupe: re-runs of the same ingestion event (a
        # webhook retry, an agent run re-execution) cannot duplicate a message.
        UniqueConstraint(
            "conversation_id",
            "dedupe_key",
            name="ux_conversation_messages_conversation_dedupe",
        ),
        # Delivery token is unique within a tenant when present; NULLs are
        # permitted for provider-ingested messages and agent mirrors.
        Index(
            "ux_conversation_messages_delivery_token",
            "organization_id",
            "delivery_token",
            unique=True,
            postgresql_where=None,  # applied via DDL partial index in migration
        ),
        Index(
            "ix_conv_msg_org_conv_delivery_status",
            "organization_id",
            "conversation_id",
            "delivery_status",
        ),
        # Tenant-safe linkage with CASCADE like customer_identities: deleting a
        # conversation removes its tenant-owned messages.
        ForeignKeyConstraint(
            ["conversation_id", "organization_id"],
            ["conversations.id", "conversations.organization_id"],
            name="fk_conversation_messages_conversation_organization",
            ondelete="CASCADE",
        ),
        Index(
            "ix_conversation_messages_organization_id",
            "organization_id",
        ),
        Index(
            "ix_conversation_messages_conversation_id",
            "conversation_id",
        ),
        Index(
            "ix_conversation_messages_conversation_sent_at",
            "conversation_id",
            "sent_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    organization_id: Mapped[int] = mapped_column(
        nullable=False,
    )

    conversation_id: Mapped[int] = mapped_column(
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    external_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    dedupe_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    direction: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    visibility: Mapped[str] = mapped_column(
        String(50),
        default="public",
        nullable=False,
    )

    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    delivery_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    delivery_token: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    requested_by_subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    delivery_error_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    conversation = relationship(
        "Conversation",
        primaryjoin=(
            "and_("
            "ConversationMessage.conversation_id == Conversation.id,"
            " ConversationMessage.organization_id == Conversation.organization_id"
            ")"
        ),
        viewonly=True,
    )