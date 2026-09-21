from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage


class ConversationRepository:
    """Tenant-scoped access to conversations.

    Every read/write predicated on ``organization_id`` so a cross-tenant
    conversation id can never be reached through a tenant-facing call.
    """

    @staticmethod
    async def create(
        db: AsyncSession,
        conversation: Conversation,
    ) -> Conversation:
        db.add(conversation)

        await db.commit()
        await db.refresh(conversation)

        return conversation

    @staticmethod
    async def update_for_tenant(
        db: AsyncSession,
        conversation: Conversation,
        changes: dict,
        organization_id: int,
    ) -> Conversation:
        """Update only if the conversation belongs to the tenant organization."""
        if conversation.organization_id != organization_id:
            raise ValueError("Conversation does not belong to this organization")

        for field, value in changes.items():
            setattr(conversation, field, value)

        await db.commit()
        await db.refresh(conversation)

        return conversation

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        conversation_id: int,
        organization_id: int,
    ) -> Conversation | None:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_ticket_for_tenant(
        db: AsyncSession,
        ticket_id: int,
        organization_id: int,
    ) -> Conversation | None:
        """Canonical conversation for a ticket (at most one is expected)."""
        result = await db.execute(
            select(Conversation)
            .where(
                Conversation.ticket_id == ticket_id,
                Conversation.organization_id == organization_id,
            )
            .order_by(Conversation.id.asc())
            .limit(1)
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_external_thread_for_tenant(
        db: AsyncSession,
        provider: str,
        external_thread_id: str,
        organization_id: int,
    ) -> Conversation | None:
        result = await db.execute(
            select(Conversation).where(
                Conversation.organization_id == organization_id,
                Conversation.provider == provider,
                Conversation.external_thread_id == external_thread_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    def _escape_like(term: str) -> str:
        return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

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
    ) -> list[Conversation]:
        predicates = [Conversation.organization_id == organization_id]
        if status is not None:
            predicates.append(Conversation.status == status)
        if provider is not None:
            predicates.append(Conversation.provider == provider)
        if channel is not None:
            predicates.append(Conversation.channel == channel)
        if customer_id is not None:
            predicates.append(Conversation.customer_id == customer_id)
        if ticket_id is not None:
            predicates.append(Conversation.ticket_id == ticket_id)
        if search is not None and search.strip():
            term = cls._escape_like(search.strip())
            predicates.append(Conversation.subject.ilike(f"%{term}%", escape="\\"))

        result = await db.execute(
            select(Conversation)
            .where(*predicates)
            .order_by(
                Conversation.latest_message_at.desc().nulls_last(),
                Conversation.updated_at.desc(),
                Conversation.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        return list(result.scalars().all())

    @classmethod
    async def count_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        status: str | None = None,
        provider: str | None = None,
        channel: str | None = None,
        customer_id: int | None = None,
        ticket_id: int | None = None,
        search: str | None = None,
    ) -> int:
        predicates = [Conversation.organization_id == organization_id]
        if status is not None:
            predicates.append(Conversation.status == status)
        if provider is not None:
            predicates.append(Conversation.provider == provider)
        if channel is not None:
            predicates.append(Conversation.channel == channel)
        if customer_id is not None:
            predicates.append(Conversation.customer_id == customer_id)
        if ticket_id is not None:
            predicates.append(Conversation.ticket_id == ticket_id)
        if search is not None and search.strip():
            term = cls._escape_like(search.strip())
            predicates.append(Conversation.subject.ilike(f"%{term}%", escape="\\"))

        result = await db.execute(
            select(func.count()).select_from(Conversation).where(*predicates)
        )

        return int(result.scalar_one())

    @staticmethod
    async def count_unscoped_for_tenant(
        db: AsyncSession,
        organization_id: int,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.organization_id == organization_id)
        )

        return int(result.scalar_one())

    @staticmethod
    def _latest_public_message_ranked(organization_id: int):
        """Ranked public messages (1 = most recent) for a tenant.

        Internal notes (visibility ``internal``) never clear needs-response, so
        they are excluded here and in the preview query.
        """
        return (
            select(
                ConversationMessage.conversation_id.label("conversation_id"),
                ConversationMessage.direction.label("direction"),
                func.row_number()
                .over(
                    partition_by=ConversationMessage.conversation_id,
                    order_by=(
                        ConversationMessage.sent_at.desc().nulls_last(),
                        ConversationMessage.id.desc(),
                    ),
                )
                .label("rn"),
            )
            .where(
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.visibility == "public",
            )
            .subquery()
        )

    @classmethod
    async def needs_response_count_for_tenant(
        cls,
        db: AsyncSession,
        organization_id: int,
    ) -> int:
        """Conversations that are open AND have an inbound last public message."""
        ranked = cls._latest_public_message_ranked(organization_id)

        result = await db.execute(
            select(func.count())
            .select_from(Conversation)
            .join(ranked, ranked.c.conversation_id == Conversation.id)
            .where(
                Conversation.organization_id == organization_id,
                Conversation.status == "open",
                ranked.c.rn == 1,
                ranked.c.direction == "inbound",
            )
        )

        return int(result.scalar_one())

    @classmethod
    async def latest_public_messages_for_conversations(
        cls,
        db: AsyncSession,
        organization_id: int,
        conversation_ids: list[int],
    ) -> dict[int, ConversationMessage]:
        """Latest public message per conversation (used for list previews)."""
        if not conversation_ids:
            return {}

        ranked = cls._latest_public_message_ranked(organization_id)

        result = await db.execute(
            select(ConversationMessage, ranked.c.rn)
            .join(
                ranked,
                ranked.c.conversation_id == ConversationMessage.conversation_id,
            )
            .where(ConversationMessage.conversation_id.in_(conversation_ids))
        )

        previews: dict[int, ConversationMessage] = {}
        for message, rn in result.all():
            if rn == 1:
                previews[message.conversation_id] = message
        return previews

    @classmethod
    async def count_by_column_for_tenant(
        cls,
        db: AsyncSession,
        organization_id: int,
        column,
    ) -> dict[str, int]:
        """Grouped counts by a bounded conversation column (provider/channel)."""
        result = await db.execute(
            select(column, func.count())
            .where(Conversation.organization_id == organization_id)
            .group_by(column)
            .order_by(column.asc())
        )

        return {str(row[0]): int(row[1]) for row in result.all()}