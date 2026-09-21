import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.conversation_repository import ConversationRepository


class ConversationContextMessage(BaseModel):
    direction: str
    body: str
    sent_at: datetime | None


class ConversationContext(BaseModel):
    """Bounded, deterministic slice of a conversation handed to the agent.

    Only public messages are included (internal notes and private reply
    composing are excluded). The serialized form has a hard byte budget and is
    consumed by ``compute_digest``, which feeds the agent-run fingerprint so a
    new message changes the analysis freshness signal.
    """

    conversation_id: int
    provider: str
    channel: str
    status: str
    subject: str | None
    recent_messages: list[ConversationContextMessage] = Field(default_factory=list)
    partial: bool = False
    unavailable_sources: list[str] = Field(default_factory=list)


class ConversationContextService:
    MAX_MESSAGES = 12
    MAX_BODY_CHARS = 1000
    MAX_SERIALIZED_BYTES = 6144

    @classmethod
    async def build_for_ticket(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        ticket_id: int,
    ) -> ConversationContext | None:
        """Build a bounded conversation context for a ticket.

        Returns ``None`` when the ticket has no conversation (legacy tickets).
        """
        conversation = await ConversationRepository.get_by_ticket_for_tenant(
            db,
            ticket_id=ticket_id,
            organization_id=organization_id,
        )
        if conversation is None:
            return None

        messages = (
            await ConversationMessageRepository.list_recent_public_for_conversation_for_tenant(
                db,
                conversation_id=conversation.id,
                organization_id=organization_id,
                limit=cls.MAX_MESSAGES,
            )
        )

        context = ConversationContext(
            conversation_id=conversation.id,
            provider=conversation.provider,
            channel=conversation.channel,
            status=conversation.status,
            subject=conversation.subject,
            recent_messages=[
                ConversationContextMessage(
                    direction=m.direction,
                    body=m.body,
                    sent_at=m.sent_at,
                )
                for m in messages
            ],
        )

        cls._apply_bounds(context)
        return context

    @classmethod
    def _apply_bounds(cls, context: ConversationContext) -> None:
        """Deterministic down-sampling: cap body length, then drop oldest."""
        for message in context.recent_messages:
            if len(message.body) > cls.MAX_BODY_CHARS:
                message.body = message.body[: cls.MAX_BODY_CHARS]
                context.partial = True

        serialized = cls.to_serialized_dict(context)
        budget = len(json.dumps(serialized, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        while budget > cls.MAX_SERIALIZED_BYTES and len(context.recent_messages) > 1:
            context.recent_messages = context.recent_messages[1:]
            context.partial = True
            serialized = cls.to_serialized_dict(context)
            budget = len(
                json.dumps(serialized, separators=(",", ":"), sort_keys=True).encode("utf-8")
            )

    @staticmethod
    def to_serialized_dict(context: ConversationContext) -> dict:
        """Stable, message-body-only serialized form used for digest + prompt.

        Message bodies are treated as untrusted text; only the bounded literal
        characters are kept (no markdown/HTML processing here).
        """
        return {
            "conversation_id": context.conversation_id,
            "provider": context.provider,
            "channel": context.channel,
            "status": context.status,
            "subject": context.subject,
            "recent_messages": [
                {
                    "direction": m.direction,
                    "body": m.body,
                    "sent_at": (
                        m.sent_at.isoformat() if m.sent_at is not None else None
                    ),
                }
                for m in context.recent_messages
            ],
            "partial": context.partial,
        }

    @classmethod
    def compute_digest(cls, context: ConversationContext | None) -> str:
        """Deterministic freshness digest for the agent fingerprint.

        ``""`` (empty) is the stable sentinel for "no conversation context".
        """
        if context is None:
            return ""

        canonical = json.dumps(
            cls.to_serialized_dict(context),
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()