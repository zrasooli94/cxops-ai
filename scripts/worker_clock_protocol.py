"""Offline authentication contract for a future isolated worker-clock endpoint.

IMPORTANT: No FastAPI route, database mutation, or production deployment.
A future adapter MUST enforce durable per-slot deduplication and disabled-by-
default runtime gating before executing any worker job.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

CLOCK_PATH = "/internal/worker-clock/tick"
SLOT_MS = 300_000
MAX_AGE_MS = 900_000
MAX_FUTURE_MS = 60_000
_SIGNATURE = re.compile(r"v1=([a-f0-9]{64})\Z")
_EPOCH_MS = re.compile(r"[1-9][0-9]{12}\Z")
_KEY_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")


class InvalidClockTrigger(ValueError):
    """A generic rejection suitable for returning only HTTP 401/403."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidClockTrigger("duplicate JSON field")
        result[key] = value
    return result


def _clock_headers(
    values: Mapping[str, str] | Iterable[tuple[str, str]],
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    pairs = values.items() if isinstance(values, Mapping) else values
    for raw_key, value in pairs:
        if not isinstance(raw_key, str):
            raise InvalidClockTrigger("malformed header")
        key = raw_key.lower()
        if key in normalized:
            raise InvalidClockTrigger("duplicated clock header")
        if not isinstance(value, str):
            raise InvalidClockTrigger("malformed header")
        normalized[key] = value
    return normalized


def verify_clock_trigger(
    *,
    method: str,
    path: str,
    headers: Mapping[str, str] | Iterable[tuple[str, str]],
    body: bytes,
    secret: str,
    now_ms: int,
) -> int:
    """Verify authenticated schedule and return integer 5-minute slot.

    Does NOT prevent replay on its own. The downstream transactional handler
    must atomically claim (clock_id, slot) exactly once before any side effect.
    """
    if (
        method != "POST"
        or path != CLOCK_PATH
        or not isinstance(body, bytes)
        or len(body) > 128
    ):
        raise InvalidClockTrigger("invalid request")
    if not isinstance(secret, str) or not (32 <= len(secret) <= 256):
        raise InvalidClockTrigger("clock key missing")
    if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms <= 0:
        raise InvalidClockTrigger("invalid clock")
    hs = _clock_headers(headers)
    timestamp = hs.get("x-cxops-clock-time", "")
    if not _EPOCH_MS.fullmatch(timestamp):
        raise InvalidClockTrigger("invalid schedule time")
    scheduled_ms = int(timestamp)
    if (
        scheduled_ms % SLOT_MS
        or scheduled_ms > now_ms + MAX_FUTURE_MS
        or now_ms - scheduled_ms > MAX_AGE_MS
    ):
        raise InvalidClockTrigger("out-of-window slot")
    nonce = hs.get("x-cxops-clock-nonce", "")
    try:
        uid = UUID(nonce)
    except (ValueError, AttributeError) as exc:
        raise InvalidClockTrigger("invalid clock nonce") from exc
    if uid.version != 4 or str(uid) != nonce.lower():
        raise InvalidClockTrigger("invalid clock nonce")
    signature = hs.get("x-cxops-clock-signature", "")
    sig_match = _SIGNATURE.fullmatch(signature)
    if not sig_match:
        raise InvalidClockTrigger("invalid clock signature")
    key_id = hs.get("x-cxops-clock-key-id", "")
    if not _KEY_ID.fullmatch(key_id):
        raise InvalidClockTrigger("invalid clock key id")
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError) as exc:
        raise InvalidClockTrigger("invalid clock request JSON") from exc
    slot = scheduled_ms // SLOT_MS
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "slot"}
        or (
            type(payload["version"]) is not int
            or payload["version"] != 1
            or type(payload["slot"]) is not int
            or payload["slot"] != slot
        )
    ):
        raise InvalidClockTrigger("invalid clock slot payload")
    canonical_body = hashlib.sha256(body).hexdigest()
    canonical = (
        f"cxops-clock-v1\nPOST\n{CLOCK_PATH}\n{timestamp}\n{nonce}\n"
        f"{key_id}\n{canonical_body}"
    )
    expected = hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig_match.group(1)):
        raise InvalidClockTrigger("invalid clock signature")
    return slot
