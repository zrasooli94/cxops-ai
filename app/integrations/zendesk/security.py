import base64
import hashlib
import hmac
from datetime import datetime, timezone


def _parse_timestamp(timestamp: str) -> datetime | None:
    """Parse a Zendesk webhook timestamp.

    Accepts ISO-8601 (e.g. ``2026-09-15T10:00:00.000Z``, with or without
    fractional seconds) or a bare Unix-epoch integer string. Returns None when
    the value is unparseable so callers fail closed.
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
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def verify_zendesk_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    now: datetime | None = None,
    replay_window_seconds: int | None = None,
) -> bool:
    """Constant-time HMAC-SHA256 verification over ``timestamp + body``.

    ``timestamp`` (the exact string Zendesk sent) is part of the signed
    message, per the Zendesk signing specification. When
    ``replay_window_seconds`` is provided the parsed timestamp must fall
    within that window of ``now`` (both directions), bounding replay of a
    captured request. Signature comparison is constant-time; a missing or
    empty secret fails closed without comparison.
    """
    if not secret:
        return False

    if not signature:
        return False

    parsed = _parse_timestamp(timestamp)

    if parsed is None:
        return False

    instant = now or datetime.now(timezone.utc)

    if (
        replay_window_seconds is not None
        and replay_window_seconds > 0
        and abs((instant - parsed).total_seconds()) > replay_window_seconds
    ):
        return False

    message = timestamp.encode("utf-8") + body

    digest = hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).digest()

    expected_signature = base64.b64encode(digest).decode("utf-8")

    return hmac.compare_digest(
        expected_signature,
        signature,
    )
