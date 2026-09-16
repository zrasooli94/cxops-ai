"""Tests proving Zendesk secrets are stored as ciphertext, not plaintext."""

import base64
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

os.environ["ENVIRONMENT"] = "development"
os.environ["ENCRYPTION_KEYS"] = base64.urlsafe_b64encode(b"0" * 32).decode()
os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "false"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.encryption import CIPHER_PREFIX, is_encrypted_text
from app.core.rbac import OrganizationRole
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.zendesk_oauth_token import ZendeskOAuthToken
from app.repositories.zendesk_oauth_token_repository import (
    ZendeskOAuthTokenRepository,
)
from app.services.zendesk_oauth_service import ZendeskOAuthService

USER = "storage-test-user"


@pytest.fixture(autouse=True)
def _configure_env(monkeypatch):
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "z" * 32,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": "test-storage-issuer",
        "AUTH_JWT_AUDIENCE": "test-storage-audience",
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
        "ZENDESK_SUBDOMAIN": "cxops-test",
        "ZENDESK_CLIENT_ID": "test-client",
        "ZENDESK_CLIENT_SECRET": "test-client-secret",
        "ZENDESK_REDIRECT_URI": "http://testserver/auth/zendesk/callback",
        "ZENDESK_OAUTH_SCOPE": "read write",
        "ENCRYPTION_KEYS": base64.urlsafe_b64encode(b"0" * 32).decode(),
        "ENCRYPTION_ALLOW_LEGACY_PLAINTEXT": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def org_and_user(db):
    await db.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.subject == USER
        )
    )
    await db.commit()

    org = Organization(name=f"storage-test-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.commit()
    await db.refresh(org)

    membership = OrganizationMembership(
        subject=USER, organization_id=org.id, role=OrganizationRole.OWNER
    )
    db.add(membership)
    await db.commit()

    org_id = org.id
    try:
        yield org
    finally:
        await db.rollback()
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.subject == USER
            )
        )
        await db.execute(
            delete(ZendeskOAuthToken).where(
                ZendeskOAuthToken.organization_id == org_id
            )
        )
        await db.execute(delete(Organization).where(Organization.id == org_id))
        await db.commit()


async def _raw_secret(db, token_id: int) -> tuple[str, str | None, str | None]:
    result = await db.execute(
        text(
            "SELECT access_token, refresh_token, webhook_secret "
            "FROM zendesk_oauth_tokens WHERE id = :id"
        ),
        {"id": token_id},
    )
    return result.one()


@pytest.mark.asyncio
async def test_oauth_callback_stores_access_token_encrypted(db, org_and_user):
    async def fake_exchange(
        db_session,
        *,
        code: str,
        organization_id: int,
        subject: str,
    ):
        return await ZendeskOAuthTokenRepository.save_for_organization(
            db=db_session,
            organization_id=organization_id,
            access_token="plain-access-from-provider",
            refresh_token="plain-refresh-from-provider",
            token_type="bearer",
            scope="read write",
            expires_at=datetime.now(timezone.utc) + timedelta(
                seconds=3600
            ),
            refresh_token_expires_at=None,
            connected_by=subject,
            integration_id=f"int-{uuid.uuid4().hex[:16]}",
        )

    from app.services import zendesk_oauth_service

    original_exchange = zendesk_oauth_service.ZendeskOAuthService.exchange_code
    zendesk_oauth_service.ZendeskOAuthService.exchange_code = staticmethod(
        fake_exchange
    )

    try:
        token = await ZendeskOAuthService.exchange_code(
            db,
            code="auth-code",
            organization_id=org_and_user.id,
            subject=USER,
        )
    finally:
        zendesk_oauth_service.ZendeskOAuthService.exchange_code = original_exchange

    access_token, refresh_token, webhook_secret = await _raw_secret(db, token.id)

    assert access_token != "plain-access-from-provider"
    assert is_encrypted_text(access_token)
    assert access_token.startswith(CIPHER_PREFIX)

    assert refresh_token != "plain-refresh-from-provider"
    assert is_encrypted_text(refresh_token)

    assert webhook_secret is None


@pytest.mark.asyncio
async def test_refresh_stores_new_tokens_encrypted(db, org_and_user):
    token = ZendeskOAuthToken(
        organization_id=org_and_user.id,
        integration_id=f"refresh-int-{uuid.uuid4().hex[:16]}",
        access_token="old-access",
        refresh_token="old-refresh",
        token_type="bearer",
        scope="read write",
        expires_at=datetime.now(timezone.utc) + timedelta(
            seconds=1
        ),
        refresh_token_expires_at=None,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    async def fake_post(self, url, *, json=None, **kwargs):
        class Response:
            is_error = False
            status_code = 200

            def json(self):
                return {
                    "access_token": "refreshed-access",
                    "refresh_token": "refreshed-refresh",
                    "token_type": "bearer",
                    "expires_in": 3600,
                }

        return Response()

    import httpx

    original_post = httpx.AsyncClient.post
    httpx.AsyncClient.post = fake_post

    try:
        refreshed = await ZendeskOAuthService.refresh_access_token(
            db,
            refresh_token="old-refresh",
            organization_id=org_and_user.id,
        )
    finally:
        httpx.AsyncClient.post = original_post

    access_token, refresh_token, _ = await _raw_secret(db, refreshed.id)

    assert access_token != "refreshed-access"
    assert is_encrypted_text(access_token)
    assert access_token.startswith(CIPHER_PREFIX)

    assert refresh_token != "refreshed-refresh"
    assert is_encrypted_text(refresh_token)


@pytest.mark.asyncio
async def test_webhook_secret_provisioning_stores_ciphertext(db, org_and_user):
    token = ZendeskOAuthToken(
        organization_id=org_and_user.id,
        integration_id=f"wh-int-{uuid.uuid4().hex[:16]}",
        access_token="access",
        refresh_token=None,
        token_type="bearer",
        scope="read write",
        expires_at=datetime.now(timezone.utc) + timedelta(
            seconds=3600
        ),
        refresh_token_expires_at=None,
        webhook_secret=None,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    updated = await ZendeskOAuthTokenRepository.set_webhook_secret(
        db,
        organization_id=org_and_user.id,
        webhook_secret="super-secret-webhook-key",
    )
    assert updated is not None

    _, _, raw_secret = await _raw_secret(db, token.id)

    assert raw_secret != "super-secret-webhook-key"
    assert is_encrypted_text(raw_secret)
    assert raw_secret.startswith(CIPHER_PREFIX)

    # ORM returns plaintext.
    loaded = await db.get(ZendeskOAuthToken, token.id)
    assert loaded is not None
    assert loaded.webhook_secret == "super-secret-webhook-key"
