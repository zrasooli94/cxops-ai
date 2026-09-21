from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Conversation(Base):
    """A provider-neutral customer thread.

    A conversation is the object a support team works against: it aggregates
    messages across a single provider thread (``external_thread_id``), always
    tenant-owned, and may reference at most one customer and one ticket owned
    by the same organization. ``provider``/``channel`` are bounded
    (zendesk/cxops and ticket/web/email/whatsapp/chat); no provider payload is
    ever stored here.
    """

    __tablename__ = "conversations"

    __table_args__ = (
        # One conversation per provider thread within a tenant. Local tickets
        # use a NULL external_thread_id: Postgres allows unlimited NULLs in a
        # UNIQUE column group, so tenant-local tickets never conflate.
        UniqueConstraint(
            "organization_id",
            "provider",
            "external_thread_id",
            name="ux_conversations_tenant_provider_thread",
        ),
        # Composite unique that exists solely as the target for the tenant-safe
        # conversation_message FK (conversation_id, organization_id).
        UniqueConstraint(
            "id",
            "organization_id",
            name="ux_conversations_id_organization_id",
        ),
        # Tenant-safe customer linkage: a conversation may only reference a
        # customer owned by the same organization. CASCADE matches the thread
        # lifecycle (a conversation's messages already cascade with it) and
        # keeps test teardowns and future customer teardown paths coherent.
        ForeignKeyConstraint(
            ["customer_id", "organization_id"],
            ["customers.id", "customers.organization_id"],
            name="fk_conversations_customer_organization_customers",
            ondelete="CASCADE",
        ),
        # Tenant-safe ticket linkage: a conversation may only reference a
        # ticket owned by the same organization. CASCADE removes the thread
        # (and its messages) exactly when the ticket that scopes it disappears.
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            name="fk_conversations_ticket_organization_tickets",
            ondelete="CASCADE",
        ),
        Index(
            "ix_conversations_organization_id",
            "organization_id",
        ),
        Index(
            "ix_conversations_customer_id",
            "customer_id",
        ),
        Index(
            "ix_conversations_ticket_id",
            "ticket_id",
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

    customer_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    ticket_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    channel: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    external_thread_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="open",
        nullable=False,
    )

    latest_message_at: Mapped[datetime | None] = mapped_column(
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

    organization = relationship("Organization", viewonly=True)
    customer = relationship(
        "Customer",
        primaryjoin=(
            "and_("
            "Conversation.customer_id == Customer.id,"
            " Conversation.organization_id == Customer.organization_id"
            ")"
        ),
        viewonly=True,
    )
    ticket = relationship(
        "Ticket",
        primaryjoin=(
            "and_("
            "Conversation.ticket_id == Ticket.id,"
            " Conversation.organization_id == Ticket.organization_id"
            ")"
        ),
        viewonly=True,
    )
    messages = relationship(
        "ConversationMessage",
        primaryjoin=(
            "and_("
            "ConversationMessage.conversation_id == Conversation.id,"
            " ConversationMessage.organization_id == Conversation.organization_id"
            ")"
        ),
        viewonly=True,
        order_by="ConversationMessage.sent_at, ConversationMessage.id",
    )

    @property
    def is_open(self) -> bool:
        return self.status == "open"