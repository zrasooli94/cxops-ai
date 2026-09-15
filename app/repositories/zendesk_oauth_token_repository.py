from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.zendesk_oauth_token import ZendeskOAuthToken


class ZendeskOAuthTokenRepository:
    """Credential persistence is organization-scoped.

    There is intentionally NO ``get_latest``-style global lookup: every retrieval
    starts from an organization_id (tenant-facing) or a trusted integration_id
    (machine webhook path). Legacy rows with organization_id NULL are inert and
    are never returned by these queries.
    """

    @staticmethod
    async def get_for_organization(
        db: AsyncSession,
        organization_id: int,
    ) -> ZendeskOAuthToken | None:
        """Return the organization's Zendesk connection, if any."""
        result = await db.execute(
            select(ZendeskOAuthToken).where(
                ZendeskOAuthToken.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_integration_id(
        db: AsyncSession,
        integration_id: str,
    ) -> ZendeskOAuthToken | None:
        """Resolve a connection by its webhook integration identifier."""
        result = await db.execute(
            select(ZendeskOAuthToken).where(
                ZendeskOAuthToken.integration_id == integration_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def save_for_organization(
        db: AsyncSession,
        *,
        organization_id: int,
        access_token: str,
        refresh_token: str | None,
        token_type: str,
        scope: str | None,
        expires_at,
        refresh_token_expires_at,
        connected_by: str | None,
        integration_id: str,
    ) -> ZendeskOAuthToken:
        """Create or update the organization's single Zendesk connection.

        Re-connecting keeps the existing ``integration_id`` (and therefore the
        webhook endpoint + per-org secret) stable across token refreshes.
        """
        existing = await ZendeskOAuthTokenRepository.get_for_organization(
            db,
            organization_id,
        )

        if existing:
            existing.access_token = access_token
            existing.refresh_token = refresh_token
            existing.token_type = token_type
            existing.scope = scope
            existing.expires_at = expires_at
            existing.refresh_token_expires_at = refresh_token_expires_at
            existing.connected_by = connected_by

            await db.commit()
            await db.refresh(existing)

            return existing

        token = ZendeskOAuthToken(
            organization_id=organization_id,
            integration_id=integration_id,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=token_type,
            scope=scope,
            expires_at=expires_at,
            refresh_token_expires_at=refresh_token_expires_at,
            connected_by=connected_by,
        )

        db.add(token)

        await db.commit()
        await db.refresh(token)

        return token

    @staticmethod
    async def set_webhook_secret(
        db: AsyncSession,
        *,
        organization_id: int,
        webhook_secret: str | None,
    ) -> ZendeskOAuthToken | None:
        """Provision/clear the organization's webhook signing secret."""
        token = await ZendeskOAuthTokenRepository.get_for_organization(
            db,
            organization_id,
        )

        if token is None:
            return None

        token.webhook_secret = webhook_secret

        await db.commit()
        await db.refresh(token)

        return token
