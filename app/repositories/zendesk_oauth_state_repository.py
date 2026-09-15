from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.zendesk_oauth_state import ZendeskOAuthState


class ZendeskOAuthStateRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        state: str,
        organization_id: int,
        subject: str,
        expires_at: datetime,
    ) -> ZendeskOAuthState:
        oauth_state = ZendeskOAuthState(
            state=state,
            organization_id=organization_id,
            subject=subject,
            expires_at=expires_at,
        )

        db.add(oauth_state)

        await db.commit()
        await db.refresh(oauth_state)

        return oauth_state

    @staticmethod
    async def consume(
        db: AsyncSession,
        *,
        state: str,
        now: datetime | None = None,
    ) -> ZendeskOAuthState | None:
        """Atomically claim a state for one-time use.

        The UPDATE only claims rows that are unconsumed and unexpired, so a
        replay of an old callback or an expired state returns None.
        """
        instant = now or datetime.now(timezone.utc)

        result = await db.execute(
            update(ZendeskOAuthState)
            .where(
                ZendeskOAuthState.state == state,
                ZendeskOAuthState.consumed_at.is_(None),
                ZendeskOAuthState.expires_at > instant,
            )
            .values(consumed_at=instant)
            .returning(ZendeskOAuthState.id)
        )

        claimed_id: int | None = result.scalar_one_or_none()

        await db.commit()

        if claimed_id is None:
            return None

        claimed = await db.get(ZendeskOAuthState, claimed_id)

        return claimed
