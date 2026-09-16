"""Tests for the authenticated-encryption layer used for stored secrets."""

import base64
import os

import pytest
from cryptography.fernet import Fernet

os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.encryption import (
    EncryptedText,
    EncryptionConfigurationError,
    EncryptionError,
    decrypt_text,
    encrypt_text,
    is_encrypted_text,
    is_legacy_encrypted_text,
    rotate_ciphertext,
)

CIPHER_PREFIX = "$cxops-fernet-v1$"
TEST_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()
ALT_KEY = base64.urlsafe_b64encode(b"1" * 32).decode()


def _configure(monkeypatch, **overrides) -> None:
    values = {
        "ENVIRONMENT": "development",
        "ENCRYPTION_ALLOW_LEGACY_PLAINTEXT": "False",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()


class TestEncryptionRoundTrip:
    def test_encrypt_decrypt_roundtrip(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        plaintext = "super-secret-token"
        ciphertext = encrypt_text(plaintext)

        assert ciphertext is not None
        assert ciphertext != plaintext
        assert ciphertext.startswith(CIPHER_PREFIX)

        assert decrypt_text(ciphertext) == plaintext

    def test_none_passes_through(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        assert encrypt_text(None) is None
        assert decrypt_text(None) is None

    def test_empty_string_passes_through(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        assert encrypt_text("") == ""
        assert decrypt_text("") == ""

    def test_double_encryption_is_idempotent(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        plaintext = "token"
        once = encrypt_text(plaintext)
        twice = encrypt_text(once)

        assert twice == once
        assert decrypt_text(twice) == plaintext

    def test_encryption_requires_string(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        with pytest.raises(EncryptionError):
            encrypt_text(12345)


class TestStrictPlaintextMode:
    def test_plaintext_rejected_in_strict_mode(self, monkeypatch):
        _configure(
            monkeypatch,
            ENCRYPTION_KEYS=TEST_KEY,
            ENCRYPTION_ALLOW_LEGACY_PLAINTEXT="False",
        )
        with pytest.raises(EncryptionConfigurationError):
            decrypt_text("legacy-plaintext")

    def test_plaintext_accepted_when_compatibility_enabled(self, monkeypatch):
        _configure(
            monkeypatch,
            ENCRYPTION_KEYS=TEST_KEY,
            ENCRYPTION_ALLOW_LEGACY_PLAINTEXT="True",
        )
        assert decrypt_text("legacy-plaintext") == "legacy-plaintext"

    def test_ciphertext_still_works_in_strict_mode(self, monkeypatch):
        _configure(
            monkeypatch,
            ENCRYPTION_KEYS=TEST_KEY,
            ENCRYPTION_ALLOW_LEGACY_PLAINTEXT="False",
        )
        ciphertext = encrypt_text("secret")
        assert decrypt_text(ciphertext) == "secret"

    def test_malformed_prefixed_ciphertext_fails_closed(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        with pytest.raises(EncryptionError):
            decrypt_text(f"{CIPHER_PREFIX}not-a-valid-fernet-token")

    def test_legacy_ciphertext_decrypts_in_strict_mode(self, monkeypatch):
        _configure(
            monkeypatch,
            ENCRYPTION_KEYS=TEST_KEY,
            ENCRYPTION_ALLOW_LEGACY_PLAINTEXT="False",
        )
        fernet = Fernet(TEST_KEY.encode())
        legacy = f"$f${fernet.encrypt(b'legacy-value').decode()}"
        # Legacy prefix is still ciphertext, so strict mode must decrypt it.
        assert decrypt_text(legacy) == "legacy-value"

    def test_malformed_legacy_prefix_fails_closed(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        collision = "$f$some-real-secret"
        assert not is_encrypted_text(collision)
        assert is_legacy_encrypted_text(collision)
        with pytest.raises(EncryptionError):
            decrypt_text(collision)

    def test_compatibility_mode_returns_plaintext_unchanged(self, monkeypatch):
        _configure(
            monkeypatch,
            ENCRYPTION_KEYS=TEST_KEY,
            ENCRYPTION_ALLOW_LEGACY_PLAINTEXT="True",
        )
        plaintext = "plain-legacy-secret"
        # Legacy compatibility returns non-prefixed values as plaintext,
        # which is acceptable only during migration of uncommitted dev data.
        assert decrypt_text(plaintext) == plaintext


class TestKeyRotation:
    def test_primary_key_encrypts_and_decrypts(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=f"{TEST_KEY},{ALT_KEY}")
        plaintext = "rotate-me"
        ciphertext = encrypt_text(plaintext)

        assert decrypt_text(ciphertext) == plaintext

    def test_old_key_can_still_decrypt(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        ciphertext = encrypt_text("rotated")

        # New primary key, old key retained for decryption.
        _configure(monkeypatch, ENCRYPTION_KEYS=f"{ALT_KEY},{TEST_KEY}")
        assert decrypt_text(ciphertext) == "rotated"

    def test_removed_key_cannot_decrypt(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        ciphertext = encrypt_text("lost")

        _configure(monkeypatch, ENCRYPTION_KEYS=ALT_KEY)
        with pytest.raises(EncryptionError):
            decrypt_text(ciphertext)

    def test_rotate_ciphertext_changes_token(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        ciphertext = encrypt_text("rotate-me")

        _configure(monkeypatch, ENCRYPTION_KEYS=f"{ALT_KEY},{TEST_KEY}")
        rotated = rotate_ciphertext(ciphertext)

        assert rotated != ciphertext
        assert is_encrypted_text(rotated)

        # After rotation, only the new active key is needed.
        _configure(monkeypatch, ENCRYPTION_KEYS=ALT_KEY)
        assert decrypt_text(rotated) == "rotate-me"

    def test_rotate_legacy_prefix_to_current_prefix(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        # Simulate a legacy development ciphertext.
        fernet = Fernet(TEST_KEY.encode())
        legacy = f"$f${fernet.encrypt(b'legacy-value').decode()}"

        _configure(monkeypatch, ENCRYPTION_KEYS=f"{ALT_KEY},{TEST_KEY}")
        rotated = rotate_ciphertext(legacy)

        assert is_encrypted_text(rotated)
        assert not is_legacy_encrypted_text(rotated)
        _configure(monkeypatch, ENCRYPTION_KEYS=ALT_KEY)
        assert decrypt_text(rotated) == "legacy-value"

    def test_rotate_plaintext_raises(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        with pytest.raises(EncryptionError):
            rotate_ciphertext("plaintext")


class TestConfiguration:
    def test_missing_key_development_noop(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS="")
        plaintext = "dev-token"
        assert encrypt_text(plaintext) == plaintext

    def test_encrypted_value_without_key_fails_closed(self, monkeypatch):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        ciphertext = encrypt_text("locked")

        _configure(monkeypatch, ENCRYPTION_KEYS="")
        with pytest.raises(EncryptionConfigurationError):
            decrypt_text(ciphertext)

    def test_invalid_key_rejected_during_validation(self, monkeypatch):
        from app.core.encryption import validate_encryption_keys

        with pytest.raises(ValueError, match="Invalid Fernet key"):
            validate_encryption_keys("not-a-valid-fernet-key")

    def test_empty_key_parts_rejected_during_validation(self, monkeypatch):
        from app.core.encryption import validate_encryption_keys

        with pytest.raises(ValueError, match="Empty key"):
            validate_encryption_keys(f"{TEST_KEY}, ,{ALT_KEY}")

    def test_dev_noop_logs_warning(self, monkeypatch, capfd):
        _configure(monkeypatch, ENCRYPTION_KEYS="")
        encrypt_text("dev-token")
        captured = capfd.readouterr()
        assert "encryption_no_keys_configured" in captured.out
        assert "value_length" not in captured.out

    def test_encrypted_text_load_failure_logs_and_raises(self, monkeypatch, capfd):
        _configure(monkeypatch, ENCRYPTION_KEYS=TEST_KEY)
        column = EncryptedText()
        with pytest.raises(EncryptionError):
            column.process_result_value(
                f"{CIPHER_PREFIX}not-a-valid-token", dialect=None
            )
        captured = capfd.readouterr()
        assert "encrypted_text_load_failed" in captured.out


class TestProductionValidation:
    def test_production_requires_encryption_keys(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_MODE", "jwks")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://example.com/.well-known/jwks.json")
        monkeypatch.setenv("ENCRYPTION_KEYS", "")
        from app.core.config import Settings

        with pytest.raises(ValueError, match="ENCRYPTION_KEYS"):
            Settings()

    def test_production_starts_with_keys(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_MODE", "jwks")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://example.com/.well-known/jwks.json")
        monkeypatch.setenv("ENCRYPTION_KEYS", TEST_KEY)
        from app.core.config import Settings

        settings = Settings()
        assert settings.encryption_keys == TEST_KEY

    def test_production_rejects_malformed_keys(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_MODE", "jwks")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://example.com/.well-known/jwks.json")
        monkeypatch.setenv("ENCRYPTION_KEYS", "not-a-valid-key")
        from app.core.config import Settings

        with pytest.raises(ValueError, match="Invalid Fernet key"):
            Settings()

    def test_production_rejects_empty_key_list(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_MODE", "jwks")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://example.com/.well-known/jwks.json")
        monkeypatch.setenv("ENCRYPTION_KEYS", ", ,")
        from app.core.config import Settings

        with pytest.raises(ValueError, match="ENCRYPTION_KEYS"):
            Settings()
