from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_message import ConversationMessage
from app.models.public_chat import PublicChatConfiguration, PublicChatSession


class PublicChatRepository:
    """Tenant/scoped access to public-chat configuration and sessions.

    Every lookup is keyed by a server-derived digest or by an id plus the
    tenant resolved from server-side data (never from a client payload), so a
    cross-tenant configuration or session id can never be reached.
    """

    @staticmethod
    async def get_config_by_key_hash(
        db: AsyncSession,
        public_widget_key_hash: str,
    ) -> PublicChatConfiguration | None:
        result = await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.public_widget_key_hash == public_widget_key_hash
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def get_config_by_id_for_tenant(
        db: AsyncSession,
        configuration_id: int,
        organization_id: int,
    ) -> PublicChatConfiguration | None:
        result = await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.id == configuration_id,
                PublicChatConfiguration.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def create_session(
        db: AsyncSession,
        session: PublicChatSession,
    ) -> PublicChatSession:
        db.add(session)

        await db.commit()
        await db.refresh(session)

        return session

    @staticmethod
    async def get_session_by_token_hash(
        db: AsyncSession,
        token_hash: str,
    ) -> PublicChatSession | None:
        result = await db.execute(
            select(PublicChatSession).where(
                PublicChatSession.token_hash == token_hash
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def get_session_by_id_for_tenant(
        db: AsyncSession,
        session_id: int,
        organization_id: int,
    ) -> PublicChatSession | None:
        result = await db.execute(
            select(PublicChatSession).where(
                PublicChatSession.id == session_id,
                PublicChatSession.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def update_session_for_tenant(
        db: AsyncSession,
        session: PublicChatSession,
        changes: dict,
        organization_id: int,
    ) -> PublicChatSession:
        if session.organization_id != organization_id:
            raise ValueError("Session does not belong to this organization")

        for field, value in changes.items():
            setattr(session, field, value)

        await db.commit()
        await db.refresh(session)

        return session

    @staticmethod
    async def count_active_sessions_for_config(
        db: AsyncSession,
        configuration_id: int,
        now: datetime,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(PublicChatSession)
            .where(
                PublicChatSession.configuration_id == configuration_id,
                PublicChatSession.status != "closed",
                PublicChatSession.expires_at > now,
            )
        )

        return int(result.scalar_one())

    @staticmethod
    async def count_sessions_created_since_for_config(
        db: AsyncSession,
        configuration_id: int,
        since: datetime,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(PublicChatSession)
            .where(
                PublicChatSession.configuration_id == configuration_id,
                PublicChatSession.created_at >= since,
            )
        )

        return int(result.scalar_one())

    @staticmethod
    async def count_messages_since_for_session(
        db: AsyncSession,
        conversation_id: int,
        organization_id: int,
        since: datetime,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.direction == "inbound",
                ConversationMessage.created_at >= since,
            )
        )

        return int(result.scalar_one())

    @staticmethod
    async def list_public_messages(
        db: AsyncSession,
        conversation_id: int,
        organization_id: int,
        *,
        limit: int = 50,
    ) -> list[ConversationMessage]:
        result = await db.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.visibility == "public",
            )
            .order_by(ConversationMessage.sent_at.asc().nulls_last(), ConversationMessage.id.asc())
            .limit(limit)
        )

        return list(result.scalars().all())