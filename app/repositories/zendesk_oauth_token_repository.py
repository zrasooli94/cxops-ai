from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.zendesk_oauth_token import ZendeskOAuthToken


class ZendeskOAuthTokenRepository:

    @staticmethod
    async def get_latest(
        db: AsyncSession,
    ) -> ZendeskOAuthToken | None:

        result = await db.execute(
            select(ZendeskOAuthToken)
            .order_by(ZendeskOAuthToken.id.desc())
            .limit(1)
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def save(
        db: AsyncSession,
        *,
        access_token: str,
        refresh_token: str | None,
        token_type: str,
        scope: str | None,
        expires_at,
        refresh_token_expires_at,
    ) -> ZendeskOAuthToken:

        existing = await ZendeskOAuthTokenRepository.get_latest(
            db
        )

        if existing:
            existing.access_token = access_token
            existing.refresh_token = refresh_token
            existing.token_type = token_type
            existing.scope = scope
            existing.expires_at = expires_at
            existing.refresh_token_expires_at = (
                refresh_token_expires_at
            )

            await db.commit()
            await db.refresh(existing)

            return existing

        token = ZendeskOAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=token_type,
            scope=scope,
            expires_at=expires_at,
            refresh_token_expires_at=(
                refresh_token_expires_at
            ),
        )

        db.add(token)

        await db.commit()
        await db.refresh(token)

        return token