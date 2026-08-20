from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.zendesk_oauth_token import ZendeskOAuthToken
from app.repositories.zendesk_oauth_token_repository import (
    ZendeskOAuthTokenRepository,
)


class ZendeskOAuthError(Exception):
    pass


class ZendeskReauthorizationRequired(ZendeskOAuthError):
    pass


class ZendeskOAuthService:
    REFRESH_BUFFER_SECONDS = 60

    @staticmethod
    def build_authorization_url(
        state: str,
    ) -> str:

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

    @staticmethod
    async def exchange_code(
        db: AsyncSession,
        code: str,
    ) -> ZendeskOAuthToken:

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
            raise ZendeskOAuthError(f"{response.status_code}: {response.text}")

        return await ZendeskOAuthService._store_token_response(
            db=db,
            token_data=response.json(),
        )

    @staticmethod
    async def refresh_access_token(
        db: AsyncSession,
        refresh_token: str,
    ) -> ZendeskOAuthToken:

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
                f"Zendesk token refresh failed: {response.status_code}: {response.text}"
            )

        return await ZendeskOAuthService._store_token_response(
            db=db,
            token_data=response.json(),
        )

    @staticmethod
    async def get_valid_token(
        db: AsyncSession,
    ) -> ZendeskOAuthToken:

        token = await ZendeskOAuthTokenRepository.get_latest(db)

        if token is None:
            raise ZendeskReauthorizationRequired("Zendesk is not connected")

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

        return await ZendeskOAuthService.refresh_access_token(
            db=db,
            refresh_token=token.refresh_token,
        )

    @staticmethod
    async def _store_token_response(
        db: AsyncSession,
        token_data: dict,
    ) -> ZendeskOAuthToken:

        now = datetime.now(timezone.utc)

        expires_in = token_data.get("expires_in")
        refresh_expires_in = token_data.get("refresh_token_expires_in")

        expires_at = now + timedelta(seconds=expires_in) if expires_in else None

        refresh_token_expires_at = (
            now + timedelta(seconds=refresh_expires_in) if refresh_expires_in else None
        )

        return await ZendeskOAuthTokenRepository.save(
            db=db,
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_type=token_data.get(
                "token_type",
                "bearer",
            ),
            scope=token_data.get("scope"),
            expires_at=expires_at,
            refresh_token_expires_at=(refresh_token_expires_at),
        )
