"""One-way migration and key rotation for Zendesk secrets at rest.

Usage:
    export ENCRYPTION_KEYS='<primary-fernet-key>[,<old-key-1>,...]'

    # Encrypt plaintext rows
    python -m scripts.encrypt_zendesk_secrets [--dry-run]

    # Rotate existing ciphertext to the current primary key
    python -m scripts.encrypt_zendesk_secrets --rotate [--dry-run]

This script does NOT contain or generate keys. It reads the active key(s) from
``ENCRYPTION_KEYS``. Plaintext rows are encrypted; already-encrypted rows are
left untouched in the default mode. In ``--rotate`` mode, every encrypted value
is decrypted with the configured key set and re-encrypted under the primary key.

Run against a backup. The migration is idempotent.
"""

import argparse
import asyncio

from sqlalchemy import text, update

from app.core.database import AsyncSessionLocal
from app.core.encryption import (
    EncryptionError,
    _get_fernet,
    encrypt_text,
    is_encrypted_text,
    is_legacy_encrypted_text,
    rotate_ciphertext,
)
from app.core.logging import get_logger
from app.models.zendesk_oauth_token import ZendeskOAuthToken

log = get_logger(__name__)

_SECRET_COLUMNS = ("access_token", "refresh_token", "webhook_secret")

_RAW_SELECT = text(
    "SELECT id, access_token, refresh_token, webhook_secret FROM zendesk_oauth_tokens"
)


def _transform_value(raw: str | None, *, rotate: bool) -> str | None:
    """Return the value as it should be stored.

    - ``None``/empty pass through.
    - Plaintext is encrypted.
    - Legacy-prefixed ciphertext is rewritten to the current prefix even in
      default mode so deprecated prefixes do not linger.
    - Current-prefix ciphertext is left unchanged unless ``rotate`` is True, in
      which case it is rotated to the current primary key.
    """
    if raw is None or raw == "":
        return raw

    if is_encrypted_text(raw):
        if rotate:
            return rotate_ciphertext(raw)
        return raw

    if is_legacy_encrypted_text(raw):
        return rotate_ciphertext(raw)

    return encrypt_text(raw)


async def run(*, rotate: bool, dry_run: bool) -> None:
    # Fail closed immediately if encryption is not configured. Do not touch
    # rows when we have no key to encrypt them with. We check the built Fernet
    # directly because the development no-op in encrypt_text would mask a
    # missing-key configuration.
    if _get_fernet() is None:
        raise SystemExit(
            "error: encryption is not configured; set ENCRYPTION_KEYS"
        )

    async with AsyncSessionLocal() as db:
        # Read raw column values so we can detect already-encrypted rows without
        # triggering decryption (which would fail for keys no longer configured).
        result = await db.execute(_RAW_SELECT)
        rows = result.all()

        migrated = 0
        rotated = 0
        skipped = 0

        for row in rows:
            row_id = row[0]
            updates: dict[str, str] = {}

            for idx, column in enumerate(_SECRET_COLUMNS, start=1):
                value = row[idx]
                try:
                    new_value = _transform_value(value, rotate=rotate)
                except EncryptionError as exc:
                    raise SystemExit(
                        f"error: cannot process row {row_id}: {exc}"
                    ) from exc
                if new_value is not None and new_value != value:
                    updates[column] = new_value

            if not updates:
                skipped += 1
                continue

            if rotate:
                rotated += 1
            else:
                migrated += 1

            log.info(
                "encrypt_zendesk_secret_row",
                row_id=row_id,
                rotate=rotate,
                dry_run=dry_run,
            )

            if dry_run:
                continue

            # The EncryptedText column type will encrypt plaintext values on bind.
            # For rotation, the rotated ciphertext already carries the current prefix.
            await db.execute(
                update(ZendeskOAuthToken)
                .where(ZendeskOAuthToken.id == row_id)
                .values(**updates)
            )

        if dry_run:
            print(
                f"dry-run: {migrated} plaintext row(s) would be migrated, "
                f"{rotated} ciphertext row(s) would be rotated, "
                f"{skipped} already current"
            )
            return

        await db.commit()
        print(
            f"migrated {migrated} plaintext row(s), "
            f"rotated {rotated} ciphertext row(s), "
            f"{skipped} already current"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Encrypt and/or rotate Zendesk OAuth tokens and webhook secrets."
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help=(
            "Rotate existing ciphertext to the current primary key "
            "(ENCRYPTION_KEYS must include the old key)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which rows would be changed without writing changes",
    )
    args = parser.parse_args()

    asyncio.run(run(rotate=args.rotate, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
