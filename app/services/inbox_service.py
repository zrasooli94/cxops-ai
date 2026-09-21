from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.ticket import Ticket
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.conversation_repository import (
    ConversationRepository,
)
from app.repositories.ticket_repository import TicketRepository
from app.schemas.conversation import (
    ConversationDetail,
    ConversationListItem,
    ConversationMessagePreview,
    ConversationMessageRead,
    ConversationSummaryResponse,
    ReplyMode,
)


class InboxService:
    """Read-model assembly for the unified conversation inbox."""

    @staticmethod
    def reply_mode_for(
        provider: str,
        ticket: Ticket | None,
    ) -> ReplyMode:
        """How this conversation's replies are delivered.

        - zendesk: backed by a valid Zendesk ticket and supports human replies
          delivered through Zendesk.
        - local_only: backed by a local CXOps ticket; replies are stored locally
          and are not delivered to an external provider.
        - unsupported: no ticket link or an unsupported provider; replying is
          not possible.

        Eligibility uses positive allowlists and the same canonical target
        helper as agent execution so arbitrary external_ids are never treated
        as Zendesk.
        """
        from app.services.zendesk_target_service import resolve_zendesk_execution_target

        if ticket is None:
            return "unsupported"

        if provider == "zendesk":
            if resolve_zendesk_execution_target(ticket) is not None:
                return "zendesk"
            return "unsupported"

        if provider == "cxops":
            return "local_only"

        return "unsupported"

    @staticmethod
    def _needs_response(
        conversation: Conversation,
        latest_message,
    ) -> bool:
        if conversation.status != "open":
            return False
        if latest_message is None:
            return False
        return latest_message.direction == "inbound"

    @staticmethod
    def _preview(message) -> ConversationMessagePreview | None:
        if message is None:
            return None
        return ConversationMessagePreview(
            body=message.body,
            direction=message.direction,
            visibility=message.visibility,
            sent_at=message.sent_at,
        )

    @classmethod
    async def list_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        offset: int = 0,
        limit: int = 100,
        status: str | None = None,
        provider: str | None = None,
        channel: str | None = None,
        customer_id: int | None = None,
        ticket_id: int | None = None,
        search: str | None = None,
    ):
        conversations = await ConversationRepository.list_for_tenant(
            db,
            organization_id=organization_id,
            offset=offset,
            limit=limit,
            status=status,
            provider=provider,
            channel=channel,
            customer_id=customer_id,
            ticket_id=ticket_id,
            search=search,
        )

        total = await ConversationRepository.count_for_tenant(
            db,
            organization_id=organization_id,
            status=status,
            provider=provider,
            channel=channel,
            customer_id=customer_id,
            ticket_id=ticket_id,
            search=search,
        )

        conversation_ids = [c.id for c in conversations]
        latest = (
            await ConversationRepository.latest_public_messages_for_conversations(
                db,
                organization_id=organization_id,
                conversation_ids=conversation_ids,
            )
        )

        ticket_ids = {
            c.ticket_id for c in conversations if c.ticket_id is not None
        }
        tickets = await TicketRepository.list_by_ids_for_tenant(
            db,
            list(ticket_ids),
            organization_id,
        )

        items = [
            ConversationListItem(
                id=c.id,
                provider=c.provider,
                channel=c.channel,
                external_thread_id=c.external_thread_id,
                subject=c.subject,
                status=c.status,
                customer_id=c.customer_id,
                ticket_id=c.ticket_id,
                latest_message_at=c.latest_message_at,
                needs_response=cls._needs_response(c, latest.get(c.id)),
                reply_mode=cls.reply_mode_for(
                    c.provider,
                    tickets.get(c.ticket_id) if c.ticket_id else None,
                ),
                latest_message=cls._preview(latest.get(c.id)),
            )
            for c in conversations
        ]

        return items, total

    @classmethod
    async def get_for_tenant(
        cls,
        db: AsyncSession,
        *,
        conversation_id: int,
        organization_id: int,
    ) -> ConversationDetail | None:
        conversation = await ConversationRepository.get_by_id_for_tenant(
            db,
            conversation_id=conversation_id,
            organization_id=organization_id,
        )

        if conversation is None:
            return None

        latest = (
            await ConversationRepository.latest_public_messages_for_conversations(
                db,
                organization_id=organization_id,
                conversation_ids=[conversation.id],
            )
        ).get(conversation.id)

        ticket = None
        if conversation.ticket_id is not None:
            ticket = (
                await TicketRepository.list_by_ids_for_tenant(
                    db,
                    [conversation.ticket_id],
                    organization_id,
                )
            ).get(conversation.ticket_id)

        return ConversationDetail(
            id=conversation.id,
            provider=conversation.provider,
            channel=conversation.channel,
            external_thread_id=conversation.external_thread_id,
            subject=conversation.subject,
            status=conversation.status,
            customer_id=conversation.customer_id,
            ticket_id=conversation.ticket_id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            latest_message_at=conversation.latest_message_at,
            needs_response=cls._needs_response(conversation, latest),
            reply_mode=cls.reply_mode_for(conversation.provider, ticket),
        )

    @classmethod
    async def summary_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> ConversationSummaryResponse:
        total = await ConversationRepository.count_unscoped_for_tenant(
            db,
            organization_id,
        )
        open_count = await ConversationRepository.count_for_tenant(
            db,
            organization_id=organization_id,
            status="open",
        )
        closed_count = await ConversationRepository.count_for_tenant(
            db,
            organization_id=organization_id,
            status="closed",
        )
        needs_response = (
            await ConversationRepository.needs_response_count_for_tenant(
                db,
                organization_id,
            )
        )
        by_provider = (
            await ConversationRepository.count_by_column_for_tenant(
                db,
                organization_id,
                Conversation.provider,
            )
        )
        by_channel = (
            await ConversationRepository.count_by_column_for_tenant(
                db,
                organization_id,
                Conversation.channel,
            )
        )

        return ConversationSummaryResponse(
            total=total,
            open=open_count,
            closed=closed_count,
            needs_response=needs_response,
            by_provider=by_provider,
            by_channel=by_channel,
        )

    @classmethod
    async def messages_for_tenant(
        cls,
        db: AsyncSession,
        *,
        conversation_id: int,
        organization_id: int,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ConversationMessageRead] | None:
        """Return the thread, or None when no such tenant conversation exists."""
        conversation = await ConversationRepository.get_by_id_for_tenant(
            db,
            conversation_id=conversation_id,
            organization_id=organization_id,
        )

        if conversation is None:
            return None

        messages = await ConversationMessageRepository.list_for_conversation_for_tenant(
            db,
            conversation_id=conversation_id,
            organization_id=organization_id,
            offset=offset,
            limit=limit,
        )

        return [
            ConversationMessageRead(
                id=m.id,
                conversation_id=m.conversation_id,
                provider=m.provider,
                direction=m.direction,
                visibility=m.visibility,
                body=m.body,
                sent_at=m.sent_at,
                created_at=m.created_at,
                delivery_status=m.delivery_status,
                delivered_at=m.delivered_at,
                can_retry=m.delivery_status == "failed",
            )
            for m in messages
        ]