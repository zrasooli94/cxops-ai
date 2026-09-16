"""Tests for the Zendesk secret encryption migration and rotation script."""

import base64
import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, text

os.environ["ENVIRONMENT"] = "development"
os.environ["ENCRYPTION_KEYS"] = base64.urlsafe_b64encode(b"0" * 32).decode()
os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "true"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.encryption import (
    CIPHER_PREFIX,
    is_encrypted_text,
    is_legacy_encrypted_text,
)
from app.models.organization import Organization
from app.models.zendesk_oauth_token import ZendeskOAuthToken
from scripts.encrypt_zendesk_secrets import run

# The key established at module import; tests that mutate ENCRYPTION_KEYS must
# restore this value in their teardown to keep test isolation.
_DEFAULT_KEYS = os.environ["ENCRYPTION_KEYS"]

_INSERT_TOKEN = text(
    """
    INSERT INTO zendesk_oauth_tokens
        (organization_id, integration_id, access_token, refresh_token,
         token_type, webhook_secret)
    VALUES
        (:organization_id, :integration_id, :access_token, :refresh_token,
         :token_type, :webhook_secret)
    RETURNING id
    """
)


@pytest.mark.asyncio
async def test_migration_encrypts_plaintext_rows():
    async with AsyncSessionLocal() as db:
        org = Organization(name="migration-test-org")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        result = await db.execute(
            _INSERT_TOKEN,
            {
                "organization_id": org.id,
                "integration_id": "migration-test-int",
                "access_token": "plain-access-token",
                "refresh_token": "plain-refresh-token",
                "token_type": "bearer",
                "webhook_secret": "plain-webhook-secret",
            },
        )
        token_id = result.scalar_one()
        await db.commit()

        try:
            await run(rotate=False, dry_run=False)

            raw = await db.execute(
                text(
                    "SELECT access_token, refresh_token, webhook_secret "
                    "FROM zendesk_oauth_tokens WHERE id = :id"
                ),
                {"id": token_id},
            )
            access_token, refresh_token, webhook_secret = raw.one()

            assert is_encrypted_text(access_token)
            assert is_encrypted_text(refresh_token)
            assert is_encrypted_text(webhook_secret)

            # Loading through the ORM decrypts transparently.
            loaded = await db.get(ZendeskOAuthToken, token_id)
            assert loaded is not None
            assert loaded.access_token == "plain-access-token"
            assert loaded.refresh_token == "plain-refresh-token"
            assert loaded.webhook_secret == "plain-webhook-secret"

            # Second run is a no-op (everything already encrypted).
            await run(rotate=False, dry_run=False)
        finally:
            await db.execute(
                delete(ZendeskOAuthToken).where(ZendeskOAuthToken.id == token_id)
            )
            await db.execute(delete(Organization).where(Organization.id == org.id))
            await db.commit()
            os.environ["ENCRYPTION_KEYS"] = _DEFAULT_KEYS
            os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "true"
            reset_settings_cache()


@pytest.mark.asyncio
async def test_dry_run_does_not_write():
    async with AsyncSessionLocal() as db:
        org = Organization(name="migration-dry-run-org")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        result = await db.execute(
            _INSERT_TOKEN,
            {
                "organization_id": org.id,
                "integration_id": "migration-dry-run-int",
                "access_token": "dry-access",
                "refresh_token": None,
                "token_type": "bearer",
                "webhook_secret": None,
            },
        )
        token_id = result.scalar_one()
        await db.commit()

        try:
            await run(rotate=False, dry_run=True)

            raw = await db.execute(
                text("SELECT access_token FROM zendesk_oauth_tokens WHERE id = :id"),
                {"id": token_id},
            )
            (value,) = raw.one()
            assert value == "dry-access"
        finally:
            await db.execute(
                delete(ZendeskOAuthToken).where(ZendeskOAuthToken.id == token_id)
            )
            await db.execute(delete(Organization).where(Organization.id == org.id))
            await db.commit()
            os.environ["ENCRYPTION_KEYS"] = _DEFAULT_KEYS
            os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "true"
            reset_settings_cache()


@pytest.mark.asyncio
async def test_migration_rewrites_legacy_prefix_rows():
    old_key = base64.urlsafe_b64encode(b"A" * 32).decode()

    old_fernet = Fernet(old_key.encode())
    legacy_ciphertext = f"$f${old_fernet.encrypt(b'legacy-secret').decode()}"

    async with AsyncSessionLocal() as db:
        await db.execute(delete(ZendeskOAuthToken))
        await db.commit()

        org = Organization(name="migration-legacy-org")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        result = await db.execute(
            _INSERT_TOKEN,
            {
                "organization_id": org.id,
                "integration_id": "migration-legacy-int",
                "access_token": legacy_ciphertext,
                "refresh_token": None,
                "token_type": "bearer",
                "webhook_secret": None,
            },
        )
        token_id = result.scalar_one()
        await db.commit()

        try:
            os.environ["ENCRYPTION_KEYS"] = old_key
            os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "false"
            reset_settings_cache()

            # Default migration (no --rotate) must still rewrite legacy prefix.
            await run(rotate=False, dry_run=False)

            raw = await db.execute(
                text("SELECT access_token FROM zendesk_oauth_tokens WHERE id = :id"),
                {"id": token_id},
            )
            (migrated,) = raw.one()

            assert is_encrypted_text(migrated)
            assert not is_legacy_encrypted_text(migrated)

            loaded = await db.get(ZendeskOAuthToken, token_id)
            assert loaded is not None
            assert loaded.access_token == "legacy-secret"
        finally:
            await db.execute(
                delete(ZendeskOAuthToken).where(ZendeskOAuthToken.id == token_id)
            )
            await db.execute(delete(Organization).where(Organization.id == org.id))
            await db.commit()


@pytest.mark.asyncio
async def test_run_exits_when_no_keys_configured():
    original_keys = os.environ.get("ENCRYPTION_KEYS")
    try:
        os.environ["ENCRYPTION_KEYS"] = ""
        os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "true"
        reset_settings_cache()

        with pytest.raises(SystemExit):
            await run(rotate=False, dry_run=True)
    finally:
        if original_keys is None:
            os.environ.pop("ENCRYPTION_KEYS", None)
        else:
            os.environ["ENCRYPTION_KEYS"] = original_keys
        os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "true"
        reset_settings_cache()


@pytest.mark.asyncio
async def test_rotation_rewrites_ciphertext_to_new_primary_key():
    old_key = base64.urlsafe_b64encode(b"2" * 32).decode()
    new_key = base64.urlsafe_b64encode(b"3" * 32).decode()

    # Encrypt a value using only the old key.
    old_fernet = Fernet(old_key.encode())
    old_ciphertext = (
        f"{CIPHER_PREFIX}{old_fernet.encrypt(b'secret-value').decode()}"
    )

    async with AsyncSessionLocal() as db:
        await db.execute(delete(ZendeskOAuthToken))
        await db.commit()

        org = Organization(name="rotation-test-org")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        result = await db.execute(
            _INSERT_TOKEN,
            {
                "organization_id": org.id,
                "integration_id": "rotation-test-int",
                "access_token": old_ciphertext,
                "refresh_token": None,
                "token_type": "bearer",
                "webhook_secret": None,
            },
        )
        token_id = result.scalar_one()
        await db.commit()

        try:
            os.environ["ENCRYPTION_KEYS"] = f"{new_key},{old_key}"
            os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "false"
            reset_settings_cache()

            await run(rotate=True, dry_run=False)

            raw = await db.execute(
                text("SELECT access_token FROM zendesk_oauth_tokens WHERE id = :id"),
                {"id": token_id},
            )
            (rotated,) = raw.one()

            assert is_encrypted_text(rotated)
            assert rotated != old_ciphertext

            # After rotation, only the new active key is needed.
            os.environ["ENCRYPTION_KEYS"] = new_key
            reset_settings_cache()

            loaded = await db.get(ZendeskOAuthToken, token_id)
            assert loaded is not None
            assert loaded.access_token == "secret-value"
        finally:
            await db.execute(
                delete(ZendeskOAuthToken).where(ZendeskOAuthToken.id == token_id)
            )
            await db.execute(delete(Organization).where(Organization.id == org.id))
            await db.commit()
            os.environ["ENCRYPTION_KEYS"] = _DEFAULT_KEYS
            os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "true"
            reset_settings_cache()


@pytest.mark.asyncio
async def test_rotation_dry_run_does_not_change_db():
    old_key = base64.urlsafe_b64encode(b"4" * 32).decode()
    new_key = base64.urlsafe_b64encode(b"5" * 32).decode()

    old_fernet = Fernet(old_key.encode())
    old_ciphertext = (
        f"{CIPHER_PREFIX}{old_fernet.encrypt(b'secret-value').decode()}"
    )

    async with AsyncSessionLocal() as db:
        await db.execute(delete(ZendeskOAuthToken))
        await db.commit()

        org = Organization(name="rotation-dry-run-org")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        result = await db.execute(
            _INSERT_TOKEN,
            {
                "organization_id": org.id,
                "integration_id": "rotation-dry-run-int",
                "access_token": old_ciphertext,
                "refresh_token": None,
                "token_type": "bearer",
                "webhook_secret": None,
            },
        )
        token_id = result.scalar_one()
        await db.commit()

        try:
            os.environ["ENCRYPTION_KEYS"] = f"{new_key},{old_key}"
            os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "false"
            reset_settings_cache()

            await run(rotate=True, dry_run=True)

            raw = await db.execute(
                text("SELECT access_token FROM zendesk_oauth_tokens WHERE id = :id"),
                {"id": token_id},
            )
            (value,) = raw.one()
            assert value == old_ciphertext
        finally:
            await db.execute(
                delete(ZendeskOAuthToken).where(ZendeskOAuthToken.id == token_id)
            )
            await db.execute(delete(Organization).where(Organization.id == org.id))
            await db.commit()
            os.environ["ENCRYPTION_KEYS"] = _DEFAULT_KEYS
            os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "true"
            reset_settings_cache()


@pytest.mark.asyncio
async def test_rotation_fails_when_old_key_missing():
    old_key = base64.urlsafe_b64encode(b"6" * 32).decode()
    new_key = base64.urlsafe_b64encode(b"7" * 32).decode()

    old_fernet = Fernet(old_key.encode())
    old_ciphertext = (
        f"{CIPHER_PREFIX}{old_fernet.encrypt(b'orphan-secret').decode()}"
    )

    async with AsyncSessionLocal() as db:
        await db.execute(delete(ZendeskOAuthToken))
        await db.commit()

        org = Organization(name="rotation-missing-key-org")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        result = await db.execute(
            _INSERT_TOKEN,
            {
                "organization_id": org.id,
                "integration_id": "rotation-missing-key-int",
                "access_token": old_ciphertext,
                "refresh_token": None,
                "token_type": "bearer",
                "webhook_secret": None,
            },
        )
        token_id = result.scalar_one()
        await db.commit()

        try:
            os.environ["ENCRYPTION_KEYS"] = new_key
            os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "false"
            reset_settings_cache()

            # Rotation without the old key must fail before any DB write.
            with pytest.raises(SystemExit):
                await run(rotate=True, dry_run=False)

            raw = await db.execute(
                text("SELECT access_token FROM zendesk_oauth_tokens WHERE id = :id"),
                {"id": token_id},
            )
            (value,) = raw.one()
            assert value == old_ciphertext
        finally:
            await db.execute(
                delete(ZendeskOAuthToken).where(ZendeskOAuthToken.id == token_id)
            )
            await db.execute(delete(Organization).where(Organization.id == org.id))
            await db.commit()


@pytest.mark.asyncio
async def test_rotation_rewrites_legacy_prefix_rows():
    old_key = base64.urlsafe_b64encode(b"8" * 32).decode()
    new_key = base64.urlsafe_b64encode(b"9" * 32).decode()

    old_fernet = Fernet(old_key.encode())
    legacy_ciphertext = f"$f${old_fernet.encrypt(b'legacy-secret').decode()}"

    async with AsyncSessionLocal() as db:
        await db.execute(delete(ZendeskOAuthToken))
        await db.commit()

        org = Organization(name="rotation-legacy-org")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        result = await db.execute(
            _INSERT_TOKEN,
            {
                "organization_id": org.id,
                "integration_id": "rotation-legacy-int",
                "access_token": legacy_ciphertext,
                "refresh_token": None,
                "token_type": "bearer",
                "webhook_secret": None,
            },
        )
        token_id = result.scalar_one()
        await db.commit()

        try:
            os.environ["ENCRYPTION_KEYS"] = f"{new_key},{old_key}"
            os.environ["ENCRYPTION_ALLOW_LEGACY_PLAINTEXT"] = "false"
            reset_settings_cache()

            await run(rotate=True, dry_run=False)

            raw = await db.execute(
                text("SELECT access_token FROM zendesk_oauth_tokens WHERE id = :id"),
                {"id": token_id},
            )
            (rotated,) = raw.one()

            assert is_encrypted_text(rotated)
            assert not is_legacy_encrypted_text(rotated)

            os.environ["ENCRYPTION_KEYS"] = new_key
            reset_settings_cache()

            loaded = await db.get(ZendeskOAuthToken, token_id)
            assert loaded is not None
            assert loaded.access_token == "legacy-secret"
        finally:
            await db.execute(
                delete(ZendeskOAuthToken).where(ZendeskOAuthToken.id == token_id)
            )
            await db.execute(delete(Organization).where(Organization.id == org.id))
            await db.commit()
