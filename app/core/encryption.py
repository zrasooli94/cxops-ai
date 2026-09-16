"""Authenticated encryption for secrets stored in the database.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the ``cryptography`` library.
``MultiFernet`` supports key rotation: the first configured key is used for
encryption, and any configured key can decrypt. Legacy plaintext values are
left readable only while ``ENCRYPTION_ALLOW_LEGACY_PLAINTEXT`` is enabled;
production defaults enforce strict ciphertext reads.

Keys are supplied at runtime via ``ENCRYPTION_KEYS`` (comma-separated,
primary first). They never live in source, migrations, or logs.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text, TypeDecorator

from app.core.logging import get_logger

log = get_logger(__name__)

CIPHER_PREFIX = "$cxops-fernet-v1$"
# Short prefix used during initial uncommitted development; supported only by
# the rotation/migration path so local dev data can be rewritten once.
_LEGACY_PREFIX = "$f$"


class EncryptionError(Exception):
    """Base class for encryption/decryption failures."""


class EncryptionConfigurationError(EncryptionError):
    """Encryption cannot be performed because of a configuration problem."""


def _parse_keys(raw: str) -> list[bytes]:
    """Parse and validate a comma-separated list of Fernet keys.

    Returns an empty list when ``raw`` is empty so development environments
    can run without encryption. Rejects empty parts between commas to catch
    config typos. Production startup validation rejects the empty case.
    """
    if raw.strip() == "":
        return []

    keys: list[bytes] = []

    for part in raw.split(","):
        key = part.strip()
        if not key:
            raise EncryptionConfigurationError(
                "Empty key in ENCRYPTION_KEYS (check commas/spaces)"
            )
        key_bytes = key.encode("utf-8")
        # Fernet validates key length and alphabet on construction.
        try:
            Fernet(key_bytes)
        except ValueError as exc:
            raise EncryptionConfigurationError(
                f"Invalid Fernet key in ENCRYPTION_KEYS: {exc}"
            ) from exc
        keys.append(key_bytes)

    return keys


def validate_encryption_keys(raw: str) -> list[str]:
    """Validate ``raw`` as a comma-separated Fernet key string.

    Returns the normalized key strings so configuration validation can reject
    malformed keys at startup rather than at first runtime encryption.
    Raises ``ValueError`` so Pydantic settings validation can surface the failure.
    """
    try:
        return [k.decode("utf-8") for k in _parse_keys(raw)]
    except EncryptionConfigurationError as exc:
        raise ValueError(str(exc)) from exc


@lru_cache(maxsize=8)
def _build_multi_fernet(raw_keys: str) -> MultiFernet | None:
    """Build a ``MultiFernet`` from the raw key string.

    The cache is intentionally small: key material changes rarely and only
    when settings are reloaded in tests. ``None`` means no keys are configured.
    """
    keys = _parse_keys(raw_keys)
    if not keys:
        return None
    return MultiFernet([Fernet(k) for k in keys])


def _get_fernet() -> MultiFernet | None:
    """Return the configured ``MultiFernet`` (or ``None`` in dev without keys)."""
    # Imported lazily so the module can be imported before settings are ready.
    from app.core.config import get_settings

    return _build_multi_fernet(get_settings().encryption_keys)


def clear_fernet_cache() -> None:
    """Clear the cached Fernet material; called when settings are reloaded."""
    _build_multi_fernet.cache_clear()


def _legacy_plaintext_allowed() -> bool:
    """Return whether reading non-prefixed plaintext secrets is permitted."""
    from app.core.config import get_settings

    return get_settings().encryption_allow_legacy_plaintext


def is_encrypted_text(value: str | None) -> bool:
    """Return True when ``value`` carries the current encrypted prefix."""
    return value is not None and value.startswith(CIPHER_PREFIX)


def is_legacy_encrypted_text(value: str | None) -> bool:
    """Return True when ``value`` carries the legacy development prefix."""
    return value is not None and value.startswith(_LEGACY_PREFIX)


def encrypt_text(plaintext: str | None) -> str | None:
    """Encrypt a plaintext string for database storage.

    ``None`` and the empty string pass through unchanged. Values that already
    carry the encrypted prefix are returned as-is to avoid double-encryption.
    When no keys are configured the value is returned unchanged (development
    no-op). Production startup validation prevents this state.
    """
    if plaintext is None or plaintext == "":
        return plaintext

    if not isinstance(plaintext, str):
        raise EncryptionError("encrypt_text requires a string value")

    if is_encrypted_text(plaintext):
        return plaintext

    fernet = _get_fernet()
    if fernet is None:
        # Development no-op: warn but do not fail to allow local boot without
        # a configured key. Production fails closed during settings validation.
        log.warning("encryption_no_keys_configured")
        return plaintext

    token = fernet.encrypt(plaintext.encode("utf-8"))
    return CIPHER_PREFIX + token.decode("utf-8")


def decrypt_text(ciphertext: str | None) -> str | None:
    """Decrypt a database value back to plaintext.

    Strict ciphertext mode (production default) raises ``EncryptionError`` for
    non-prefixed values, preventing arbitrary plaintext from being accepted as
    a secret. Values carrying the legacy prefix are still treated as ciphertext
    and decrypted with the configured key set, so a missed migration does not
    lock users out. When legacy plaintext compatibility is enabled, plaintext
    values are returned unchanged for migration scenarios.

    ``None`` and the empty string pass through. Raises ``EncryptionError`` if
    an encrypted value cannot be decrypted, e.g. because the key is missing or
    wrong.
    """
    if ciphertext is None or ciphertext == "":
        return ciphertext

    if not isinstance(ciphertext, str):
        raise EncryptionError("decrypt_text requires a string value")

    if is_encrypted_text(ciphertext):
        prefix = CIPHER_PREFIX
    elif is_legacy_encrypted_text(ciphertext):
        prefix = _LEGACY_PREFIX
    else:
        if _legacy_plaintext_allowed():
            return ciphertext
        raise EncryptionConfigurationError(
            "Strict ciphertext mode: refusing to read non-encrypted secret value. "
            "Run `python -m scripts.encrypt_zendesk_secrets` or set "
            "ENCRYPTION_ALLOW_LEGACY_PLAINTEXT only during migration."
        )

    fernet = _get_fernet()
    if fernet is None:
        raise EncryptionConfigurationError(
            "Encrypted database value found but no ENCRYPTION_KEYS configured"
        )

    token = ciphertext[len(prefix) :].encode("utf-8")

    try:
        plaintext = fernet.decrypt(token)
    except InvalidToken as exc:
        raise EncryptionError("Could not decrypt value: invalid token or key") from exc

    return plaintext.decode("utf-8")


def rotate_ciphertext(ciphertext: str) -> str:
    """Rotate an existing ciphertext to the current primary key.

    Accepts both the current prefix and the legacy development prefix. Decrypts
    with the configured key set and re-encrypts using the primary (first) key
    only, so old keys can be retired after rotation. Raises
    ``EncryptionError``/``EncryptionConfigurationError`` if the value is not
    ciphertext or cannot be decrypted with the configured keys.
    """
    if not isinstance(ciphertext, str) or ciphertext == "":
        raise EncryptionError("rotate_ciphertext requires a non-empty string value")

    fernet = _get_fernet()
    if fernet is None:
        raise EncryptionConfigurationError(
            "Ciphertext rotation requires ENCRYPTION_KEYS to be configured"
        )

    if is_encrypted_text(ciphertext):
        prefix = CIPHER_PREFIX
    elif is_legacy_encrypted_text(ciphertext):
        prefix = _LEGACY_PREFIX
    else:
        raise EncryptionError(
            "Value is not ciphertext; migrate plaintext before rotating"
        )

    token = ciphertext[len(prefix) :].encode("utf-8")

    try:
        plaintext = fernet.decrypt(token)
    except InvalidToken as exc:
        raise EncryptionError(
            "Could not rotate ciphertext: invalid token or key"
        ) from exc

    # Imported lazily to avoid import-time side effects.
    from app.core.config import get_settings

    primary_key = _parse_keys(get_settings().encryption_keys)[0]
    rotated = Fernet(primary_key).encrypt(plaintext)

    return CIPHER_PREFIX + rotated.decode("utf-8")


class EncryptedText(TypeDecorator[str]):
    """SQLAlchemy column type that encrypts at bind time and decrypts on load.

    Transparently protects values at rest while presenting plaintext strings
    to application code. Plaintext legacy rows are readable only when legacy
    compatibility is explicitly enabled.
    """

    impl = Text
    cache_ok = False

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        return encrypt_text(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        try:
            return decrypt_text(value)
        except EncryptionError as exc:
            log.error("encrypted_text_load_failed", error=str(exc))
            raise
