from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rbac import (
    AuthorizationContext,
    Capability,
    require_capability,
)
from app.models.zendesk_oauth_state import ZendeskOAuthState
from app.models.zendesk_oauth_token import ZendeskOAuthToken
from app.repositories.zendesk_oauth_state_repository import (
    ZendeskOAuthStateRepository,
)
from app.repositories.zendesk_oauth_token_repository import (
    ZendeskOAuthTokenRepository,
)


class ZendeskOAuthError(Exception):
    pass


class ZendeskReauthorizationRequired(ZendeskOAuthError):
    pass


class ZendeskNotConfiguredError(ZendeskOAuthError):
    """The organization has no active Zendesk integration."""


class ZendeskOAuthStateError(ZendeskOAuthError):
    """OAuth state is missing, forged, expired, or already consumed."""


class ZendeskOAuthService:
    REFRESH_BUFFER_SECONDS = 60

    @staticmethod
    def build_authorization_url(
        state: str,
    ) -> str:
        """Build the provider authorization URL carrying the opaque state nonce."""

        base_url = (
            f"https://{settings.zendesk_subdomain}.zendesk.com/oauth/authorizations/new"
        )

        params = {
            "response_type": "code",
            "client_id": settings.zendesk_client_id,
            "redirect_uri": settings.zendesk_redirect_uri,
            "scope": settings.zendesk_oauth_scope,
            "state": state,
        }

        return f"{base_url}?{urlencode(params)}"

    # ---------------------------------------------------------------
    # Durable, one-time, tenant-bound OAuth state
    # ---------------------------------------------------------------

    @staticmethod
    async def create_state(
        db: AsyncSession,
        *,
        organization_id: int,
        subject: str,
        authz: AuthorizationContext,
    ) -> str:
        """Create an unguessable nonce durably bound to an organization.

        The nonce itself carries no secret material: organization and subject
        are recovered from the persisted row at callback time.
        """
        require_capability(authz, Capability.INTEGRATION_MANAGE)

        import secrets

        nonce = secrets.token_urlsafe(32)

        ttl_seconds = settings.zendesk_oauth_state_ttl_seconds

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        await ZendeskOAuthStateRepository.create(
            db=db,
            state=nonce,
            organization_id=organization_id,
            subject=subject,
            expires_at=expires_at,
        )

        return nonce

    @staticmethod
    async def consume_state(
        db: AsyncSession,
        state: str,
    ) -> ZendeskOAuthState:
        """Atomically consume a state for a single callback use."""
        claimed = await ZendeskOAuthStateRepository.consume(
            db=db,
            state=state,
        )

        if claimed is None:
            raise ZendeskOAuthStateError(
                "OAuth state is invalid, expired, or already used"
            )

        return claimed

    # ---------------------------------------------------------------
    # Token exchange / refresh (organization-scoped)
    # ---------------------------------------------------------------

    @staticmethod
    async def exchange_code(
        db: AsyncSession,
        *,
        code: str,
        organization_id: int,
        subject: str,
    ) -> ZendeskOAuthToken:
        """Exchange an authorization code and store the credential for one org."""

        url = f"https://{settings.zendesk_subdomain}.zendesk.com/oauth/tokens"

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.zendesk_client_id,
            "client_secret": settings.zendesk_client_secret,
            "redirect_uri": settings.zendesk_redirect_uri,
        }

        async with httpx.AsyncClient(
            timeout=20.0,
        ) as client:
            response = await client.post(
                url,
                json=payload,
            )

        if response.is_error:
            raise ZendeskOAuthError(
                f"Zendesk token exchange failed (status {response.status_code})"
            )

        existing = await ZendeskOAuthTokenRepository.get_for_organization(
            db,
            organization_id,
        )

        integration_id = (
            existing.integration_id
            if existing and existing.integration_id
            else str(uuid4())
        )

        return await ZendeskOAuthService._store_token_response(
            db=db,
            token_data=response.json(),
            organization_id=organization_id,
            connected_by=subject,
            integration_id=integration_id,
        )

    @staticmethod
    async def refresh_access_token(
        db: AsyncSession,
        *,
        refresh_token: str,
        organization_id: int,
    ) -> ZendeskOAuthToken:
        """Refresh the organization's access token in place."""

        url = f"https://{settings.zendesk_subdomain}.zendesk.com/oauth/tokens"

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.zendesk_client_id,
            "client_secret": settings.zendesk_client_secret,
        }

        async with httpx.AsyncClient(
            timeout=20.0,
        ) as client:
            response = await client.post(
                url,
                json=payload,
            )

        if response.is_error:
            raise ZendeskReauthorizationRequired(
                f"Zendesk token refresh failed (status {response.status_code})"
            )

        existing = await ZendeskOAuthTokenRepository.get_for_organization(
            db,
            organization_id,
        )

        if not existing:
            raise ZendeskNotConfiguredError(
                "Zendesk integration is not configured for this organization"
            )

        integration_id = existing.integration_id or str(uuid4())

        return await ZendeskOAuthService._store_token_response(
            db=db,
            token_data=response.json(),
            organization_id=organization_id,
            connected_by=existing.connected_by,
            integration_id=integration_id,
        )

    @staticmethod
    async def get_valid_token(
        db: AsyncSession,
        organization_id: int,
    ) -> ZendeskOAuthToken:
        """Resolve + refresh the organization's Zendesk credential.

        Tenant A can only ever reach the connection stored under Tenant A's
        organization_id. A missing connection fails closed with
        ``ZendeskNotConfiguredError``.
        """
        token = await ZendeskOAuthTokenRepository.get_for_organization(
            db,
            organization_id,
        )

        if token is None:
            raise ZendeskNotConfiguredError(
                "Zendesk integration is not configured for this organization"
            )

        if token.expires_at is None:
            return token

        now = datetime.now(timezone.utc)

        refresh_at = token.expires_at - timedelta(
            seconds=ZendeskOAuthService.REFRESH_BUFFER_SECONDS
        )

        if now < refresh_at:
            return token

        if not token.refresh_token:
            raise ZendeskReauthorizationRequired("Zendesk refresh token is unavailable")

        if token.refresh_token_expires_at and now >= token.refresh_token_expires_at:
            raise ZendeskReauthorizationRequired("Zendesk refresh token has expired")

        # Token was resolved by organization_id: the row cannot carry NULL
        # ownership (legacy global rows never match a tenant-scoped lookup).
        if token.organization_id is None:
            raise ZendeskNotConfiguredError(
                "Zendesk integration is not configured for this organization"
            )

        return await ZendeskOAuthService.refresh_access_token(
            db=db,
            refresh_token=token.refresh_token,
            organization_id=token.organization_id,
        )

    @staticmethod
    async def _store_token_response(
        db: AsyncSession,
        *,
        token_data: dict,
        organization_id: int,
        connected_by: str | None,
        integration_id: str,
    ) -> ZendeskOAuthToken:

        now = datetime.now(timezone.utc)

        expires_in = token_data.get("expires_in")
        refresh_expires_in = token_data.get("refresh_token_expires_in")

        expires_at = now + timedelta(seconds=expires_in) if expires_in else None

        refresh_token_expires_at = (
            now + timedelta(seconds=refresh_expires_in) if refresh_expires_in else None
        )

        return await ZendeskOAuthTokenRepository.save_for_organization(
            db=db,
            organization_id=organization_id,
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_type=token_data.get(
                "token_type",
                "bearer",
            ),
            scope=token_data.get("scope"),
            expires_at=expires_at,
            refresh_token_expires_at=(refresh_token_expires_at),
            connected_by=connected_by,
            integration_id=integration_id,
        )
