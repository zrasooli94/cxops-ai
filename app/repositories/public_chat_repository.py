from datetime import datetime

from sqlalchemy import func, select, update
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

    @staticmethod
    async def list_handoff_sessions_for_tenant(
        db: AsyncSession,
        organization_id: int,
        *,
        limit: int = 100,
    ) -> list[PublicChatSession]:
        result = await db.execute(
            select(PublicChatSession)
            .where(
                PublicChatSession.organization_id == organization_id,
                PublicChatSession.status.in_(
                    ["human_requested", "human_assigned"]
                ),
            )
            .order_by(PublicChatSession.updated_at.desc())
            .limit(limit)
        )

        return list(result.scalars().all())

    @staticmethod
    async def _read_back_after_transition(
        db: AsyncSession,
        session_id: int,
        organization_id: int,
    ) -> PublicChatSession | None:
        """Re-read a row just changed by a conditional UPDATE.

        The transition statement opts out of identity-map synchronization, and
        the session factory disables ``expire_on_commit``. A plain re-select
        would therefore hand back the *pre-transition* cached instance, so the
        read-back must force ``populate_existing`` to overwrite already-loaded
        attributes with the committed row.
        """
        result = await db.execute(
            select(PublicChatSession)
            .where(
                PublicChatSession.id == session_id,
                PublicChatSession.organization_id == organization_id,
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def assign_session_for_tenant(
        db: AsyncSession,
        *,
        session_id: int,
        organization_id: int,
        subject: str,
        when: datetime,
    ) -> PublicChatSession | None:
        """Claim a ``human_requested`` session for a staff subject.

        The transition is a single atomic, tenant-scoped conditional UPDATE:
        the expected source status is part of the WHERE clause, so the database
        itself admits exactly one winner. Two concurrent assigns cannot both
        succeed — the loser matches no row and gets ``None`` back, which the
        caller surfaces as a 409. Tenant scoping stays in the SQL, so a
        cross-tenant session id is indistinguishable from a missing one.

        Returns ``None`` when no row matched (absent, foreign tenant, or the
        status was not ``human_requested``). The caller disambiguates those.
        """
        result = await db.execute(
            update(PublicChatSession)
            .where(
                PublicChatSession.id == session_id,
                PublicChatSession.organization_id == organization_id,
                PublicChatSession.status == "human_requested",
            )
            .values(
                status="human_assigned",
                assigned_to_subject=subject,
                assigned_at=when,
                released_at=None,
            )
            .returning(PublicChatSession.id)
            .execution_options(synchronize_session=False)
        )
        if result.mappings().first() is None:
            return None

        await db.commit()
        return await PublicChatRepository._read_back_after_transition(
            db,
            session_id,
            organization_id,
        )

    @staticmethod
    async def release_session_for_tenant(
        db: AsyncSession,
        *,
        session_id: int,
        organization_id: int,
        when: datetime,
    ) -> PublicChatSession | None:
        """Return a ``human_assigned`` session to the handoff queue.

        Mirrors :meth:`assign_session_for_tenant`: the expected source status is
        in the WHERE clause, so a release racing an assign resolves to exactly
        one legal outcome and there is no last-writer-wins.
        """
        result = await db.execute(
            update(PublicChatSession)
            .where(
                PublicChatSession.id == session_id,
                PublicChatSession.organization_id == organization_id,
                PublicChatSession.status == "human_assigned",
            )
            .values(
                status="human_requested",
                assigned_to_subject=None,
                assigned_at=None,
                released_at=when,
            )
            .returning(PublicChatSession.id)
            .execution_options(synchronize_session=False)
        )
        if result.mappings().first() is None:
            return None

        await db.commit()
        return await PublicChatRepository._read_back_after_transition(
            db,
            session_id,
            organization_id,
        )