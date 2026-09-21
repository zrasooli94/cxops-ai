from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_message import ConversationMessage


class ConversationMessageRepository:
    """Tenant-scoped access to conversation messages.

    Messages are deduplicated by ``external_message_id`` (provider truth) and by
    ``dedupe_key`` (local deterministic replay); both are enforced by database
    unique constraints, so concurrent ingestion retries cannot double-insert.
    """

    @staticmethod
    def add(
        db: AsyncSession,
        message: ConversationMessage,
    ) -> None:
        """Stage a message inside the caller's transaction (no commit)."""
        db.add(message)

    @staticmethod
    async def flush(
        db: AsyncSession,
    ) -> None:
        """Flush pending changes so generated ids are available.

        Used by the transactional outbox to obtain ``message.id`` before
        creating the dependent IntegrationJob, all inside one commit.
        """
        await db.flush()

    @staticmethod
    async def create(
        db: AsyncSession,
        message: ConversationMessage,
    ) -> ConversationMessage:
        db.add(message)

        await db.commit()
        await db.refresh(message)

        return message

    @staticmethod
    async def create_many(
        db: AsyncSession,
        messages: list[ConversationMessage],
    ) -> None:
        """Insert a bounded batch in one commit.

        On a duplicate collision the batch is rolled back (a stale preview list)
        and the caller re-queries what actually exists before retrying the
        remainder, so concurrent webhook replays converge without errors.
        """
        if not messages:
            return

        db.add_all(messages)
        await db.commit()

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        *,
        message_id: int,
        organization_id: int,
    ) -> ConversationMessage | None:
        """Tenant-scoped message lookup for worker/internal paths.

        Does NOT constrain by conversation_id; callers that need a
        conversation-bound check must use ``get_by_id_for_conversation_for_tenant``.
        """
        result = await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.id == message_id,
                ConversationMessage.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id_for_conversation_for_tenant(
        db: AsyncSession,
        *,
        message_id: int,
        conversation_id: int,
        organization_id: int,
    ) -> ConversationMessage | None:
        """Conversation-bound tenant-scoped message lookup.

        Use this for human-facing endpoints that supply both conversation_id
        and message_id, so cross-conversation retry/reply is rejected by SQL.
        """
        result = await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.id == message_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def update_for_tenant(
        db: AsyncSession,
        *,
        message: ConversationMessage,
        changes: dict,
        organization_id: int,
    ) -> ConversationMessage:
        """Update a message in place inside the caller's transaction.

        The caller is responsible for commit/rollback. This lets the delivery
        service update message state and the integration job atomically.
        """
        if message.organization_id != organization_id:
            raise ValueError("Message does not belong to this organization")

        for field, value in changes.items():
            setattr(message, field, value)

        return message

    @staticmethod
    async def get_by_delivery_token_for_tenant(
        db: AsyncSession,
        *,
        delivery_token: str,
        organization_id: int,
    ) -> ConversationMessage | None:
        result = await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.delivery_token == delivery_token,
                ConversationMessage.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_conversation_for_tenant(
        db: AsyncSession,
        *,
        conversation_id: int,
        organization_id: int,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ConversationMessage]:
        """Chronological thread (sent_at then id for ordering stability)."""
        result = await db.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.organization_id == organization_id,
            )
            .order_by(
                ConversationMessage.sent_at.asc().nulls_last(),
                ConversationMessage.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )

        return list(result.scalars().all())

    @staticmethod
    async def list_recent_public_for_conversation_for_tenant(
        db: AsyncSession,
        *,
        conversation_id: int,
        organization_id: int,
        limit: int,
    ) -> list[ConversationMessage]:
        """Most recent public messages, returned oldest-first.

        Human replies that have not yet been delivered (queued/sending/
        retrying/failed) are excluded because they are not yet real
        customer-facing conversation history.
        """
        result = await db.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.visibility == "public",
                (
                    ConversationMessage.delivery_status.is_(None)
                    | (ConversationMessage.delivery_status == "sent")
                ),
            )
            .order_by(
                ConversationMessage.sent_at.desc().nulls_last(),
                ConversationMessage.id.desc(),
            )
            .limit(limit)
        )

        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    @staticmethod
    async def get_by_dedupe_key(
        db: AsyncSession,
        *,
        conversation_id: int,
        organization_id: int,
        dedupe_key: str,
    ) -> ConversationMessage | None:
        result = await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.dedupe_key == dedupe_key,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_external_message_ids_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        provider: str,
        external_message_ids: list[str],
    ) -> dict[str, ConversationMessage]:
        """Existing messages by provider external id (conflict re-check)."""
        if not external_message_ids:
            return {}

        result = await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.provider == provider,
                ConversationMessage.external_message_id.in_(external_message_ids),
            )
        )

        return {
            m.external_message_id: m
            for m in result.scalars().all()
            if m.external_message_id is not None
        }