import hashlib
import hmac
from datetime import datetime, timezone

HEX_SIGNATURE_LENGTH = 64


def compute_signature(
    *,
    secret: str,
    body: bytes,
) -> str:
    """HMAC-SHA256 hex digest over the raw request body."""
    return hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    *,
    secret: str,
    body: bytes,
    signature: str,
) -> bool:
    """Constant-time verification of a hex HMAC-SHA256 signature.

    Only 64-char lowercase hex digests are accepted; anything else is
    rejected before comparison. Comparison uses hmac.compare_digest.
    """
    if not signature or len(signature) != HEX_SIGNATURE_LENGTH:
        return False

    try:
        bytes.fromhex(signature)
    except ValueError:
        return False

    expected = compute_signature(
        secret=secret,
        body=body,
    )

    return hmac.compare_digest(
        expected,
        signature.lower(),
    )


def _parse_timestamp(timestamp: str) -> datetime | None:
    """Parse a generic webhook ISO-8601 timestamp.

    Accepts values such as ``2026-09-15T10:00:00.000Z`` or with a numeric
    timezone offset. Returns ``None`` when the value is unparseable so
    callers fail closed.
    """
    value = timestamp.strip()

    try:
        if value.isdigit():
            ts = int(value)
            if ts <= 0 or ts > 253402300799:  # year 9999 upper bound
                return None
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def compute_signed_webhook_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
) -> str:
    """HMAC-SHA256 hex digest over ``timestamp + body``.

    The timestamp (the exact string sent in ``X-CXOps-Timestamp``) is part of
    the signed message so the verifier can bound replay windows.
    """
    message = timestamp.encode("utf-8") + body
    return hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def verify_signed_webhook_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    replay_window_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Constant-time HMAC verification with a bounded replay window.

    The signature must be a 64-character lowercase hex digest produced over
    ``timestamp + body``. The timestamp must fall within
    ``replay_window_seconds`` of ``now`` in either direction. A missing or
    empty secret fails closed without comparison.
    """
    if not secret:
        return False

    if not signature or len(signature) != HEX_SIGNATURE_LENGTH:
        return False

    try:
        bytes.fromhex(signature)
    except ValueError:
        return False

    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return False

    instant = now or datetime.now(timezone.utc)
    if abs((instant - parsed).total_seconds()) > replay_window_seconds:
        return False

    expected = compute_signed_webhook_signature(
        secret=secret,
        timestamp=timestamp,
        body=body,
    )

    return hmac.compare_digest(
        expected,
        signature.lower(),
    )
