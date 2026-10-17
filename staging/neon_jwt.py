"""Pinned, staging-only Neon Managed Better Auth JWT verifier.

Production continues to use Nhost RSA JWKS. This adapter is intentionally
limited to the independent cxops_staging Neon Auth project and cannot be
selected outside ENVIRONMENT=staging. Tokens are never logged or stored.

JWT plugin: EdDSA (Ed25519), short-lived JWT, issuer and audience equal the
auth *origin* (not the /cxops_staging/auth API path). Caller still must
authorize tenant membership using the staging database; authentication
alone grants no access to any CXOps organization.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Public deployment identity, NOT a secret. Pinning prevents an env typo from
# trusting another Neon project, notably one containing production records.
STAGING_AUTH_BASE_URL = (
    "https://ep-tiny-dawn-azixl9yk.neonauth.c-3.ap-southeast-1.aws.neon.tech"
    "/cxops_staging/auth"
)
STAGING_AUTH_ORIGIN = (
    "https://ep-tiny-dawn-azixl9yk.neonauth.c-3.ap-southeast-1.aws.neon.tech"
)
STAGING_JWKS_URL = STAGING_AUTH_BASE_URL + "/.well-known/jwks.json"

_MAX_TOKEN_SIZE = 8192
_MAX_JWKS_BYTES = 32768
_MAX_JWT_LIFETIME_SECONDS = 15 * 60 + 5
_ALLOWED_CLOCK_SKEW_SECONDS = 30
_JWKS_CACHE_TTL_SECONDS = 5 * 60
_UNKNOWN_KID_REFRESH_INTERVAL_SECONDS = 30
_JWKS_FAILURE_RETRY_SECONDS = 5
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_jwks_cache: object | None = None
_jwks_cache_expires_at = 0.0
_last_unknown_kid_refresh_at = 0.0
_jwks_failure_retry_at = 0.0
_jwks_refresh_lock = asyncio.Lock()


class NeonStagingTokenError(Exception):
    """Invalid staging token (client receives a generic 401)."""


class NeonStagingIdentityUnavailable(Exception):
    """Pinned staging JWKS temporarily unavailable (client receives 503)."""


def _decode_segment(segment: str) -> bytes:
    if (
        not segment
        or len(segment) > _MAX_TOKEN_SIZE
        or not _BASE64URL_RE.fullmatch(segment)
    ):
        raise NeonStagingTokenError("Invalid JWT encoding")
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (ValueError, binascii.Error) as exc:
        raise NeonStagingTokenError("Invalid JWT encoding") from exc


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NeonStagingTokenError("Duplicate JSON field")
        result[key] = value
    return result


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_pairs)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise NeonStagingTokenError("Invalid JWT JSON") from exc
    if not isinstance(value, dict):
        raise NeonStagingTokenError("JWT payload must be an object")
    return value


def _get_signing_key(jwks: object, kid: str) -> Ed25519PublicKey:
    if not isinstance(jwks, dict):
        raise NeonStagingIdentityUnavailable("Malformed staging JWKS")
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not (1 <= len(keys) <= 16):
        raise NeonStagingIdentityUnavailable("Malformed staging JWKS")
    candidates = [
        key for key in keys if isinstance(key, dict) and key.get("kid") == kid
    ]
    if len(candidates) != 1:
        raise NeonStagingTokenError("No unique staging signing key")
    key = candidates[0]
    if (
        key.get("kty") != "OKP"
        or key.get("crv") != "Ed25519"
        or key.get("alg", "EdDSA") != "EdDSA"
        or key.get("use", "sig") != "sig"
        or not isinstance(key.get("key_ops", ["verify"]), list)
        or ("verify" not in key.get("key_ops", ["verify"]))
    ):
        raise NeonStagingTokenError("Unsupported staging JWK")
    encoded = key.get("x")
    if not isinstance(encoded, str):
        raise NeonStagingTokenError("Missing Ed25519 key")
    try:
        raw = _decode_segment(encoded)
        if len(raw) != 32:
            raise NeonStagingTokenError("Invalid Ed25519 public key length")
        return Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError) as exc:
        raise NeonStagingTokenError("Invalid Ed25519 public key") from exc


def _prevalidate_token_header(token: str) -> str:
    """Reject malformed/unsupported JWTs before touching the shared JWKS service."""
    if not isinstance(token, str) or len(token) > _MAX_TOKEN_SIZE:
        raise NeonStagingTokenError("Malformed staging bearer token")
    parts = token.split(".")
    if len(parts) != 3:
        raise NeonStagingTokenError("Malformed staging bearer token")
    header = _json_object(_decode_segment(parts[0]))
    if header.get("alg") != "EdDSA" or header.get("typ", "JWT") != "JWT":
        raise NeonStagingTokenError("Unsupported JWT algorithm")
    kid = header.get("kid")
    if not isinstance(kid, str) or not (1 <= len(kid) <= 128):
        raise NeonStagingTokenError("Missing signing key identifier")
    # Decode every segment and enforce the Ed25519 signature size before an
    # attacker can turn a syntactically-shaped bearer into an outbound fetch.
    _json_object(_decode_segment(parts[1]))
    if len(_decode_segment(parts[2])) != 64:
        raise NeonStagingTokenError("Malformed Ed25519 signature")
    return kid


def _jwks_has_kid(jwks: object, kid: str) -> bool:
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        return False
    return any(isinstance(key, dict) and key.get("kid") == kid for key in jwks["keys"])


async def _fetch_staging_jwks() -> object:
    try:
        async with (
            httpx.AsyncClient(
                timeout=httpx.Timeout(3.0, connect=1.5),
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream(
                "GET", STAGING_JWKS_URL, headers={"Accept": "application/json"}
            ) as response,
        ):
            if response.status_code != 200:
                raise NeonStagingIdentityUnavailable("Staging JWKS unavailable")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > _MAX_JWKS_BYTES:
                    raise NeonStagingIdentityUnavailable("Oversized staging JWKS")
            return json.loads(raw)
    except (httpx.RequestError, ValueError, UnicodeError) as exc:
        raise NeonStagingIdentityUnavailable("Staging JWKS unavailable") from exc


async def _cached_staging_jwks(*, force_refresh: bool = False) -> tuple[object, bool]:
    """Return a bounded-TTL JWKS; concurrent refreshes collapse to one fetch."""
    global _jwks_cache, _jwks_cache_expires_at, _jwks_failure_retry_at
    now = time.monotonic()
    if not force_refresh and _jwks_cache is not None and now < _jwks_cache_expires_at:
        return _jwks_cache, False
    async with _jwks_refresh_lock:
        now = time.monotonic()
        if (
            not force_refresh
            and _jwks_cache is not None
            and now < _jwks_cache_expires_at
        ):
            return _jwks_cache, False
        if now < _jwks_failure_retry_at:
            raise NeonStagingIdentityUnavailable("Staging JWKS temporarily unavailable")
        try:
            jwks = await _fetch_staging_jwks()
        except NeonStagingIdentityUnavailable:
            _jwks_failure_retry_at = time.monotonic() + _JWKS_FAILURE_RETRY_SECONDS
            raise
        # Validate the complete key-set shape before caching an upstream response.
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            _jwks_failure_retry_at = time.monotonic() + _JWKS_FAILURE_RETRY_SECONDS
            raise NeonStagingIdentityUnavailable("Malformed staging JWKS")
        if not (1 <= len(jwks["keys"]) <= 16):
            _jwks_failure_retry_at = time.monotonic() + _JWKS_FAILURE_RETRY_SECONDS
            raise NeonStagingIdentityUnavailable("Malformed staging JWKS")
        _jwks_cache = jwks
        _jwks_cache_expires_at = time.monotonic() + _JWKS_CACHE_TTL_SECONDS
        _jwks_failure_retry_at = 0.0
        return jwks, True


async def _refresh_unknown_kid(kid: str) -> object:
    """Rate-limit rotation probes under the same lock as JWKS refreshes."""
    global _jwks_cache, _jwks_cache_expires_at, _jwks_failure_retry_at
    global _last_unknown_kid_refresh_at
    async with _jwks_refresh_lock:
        if _jwks_cache is not None and _jwks_has_kid(_jwks_cache, kid):
            return _jwks_cache
        now = time.monotonic()
        if (
            now < _jwks_failure_retry_at
            or now - _last_unknown_kid_refresh_at
            < _UNKNOWN_KID_REFRESH_INTERVAL_SECONDS
        ):
            if _jwks_cache is None:
                raise NeonStagingIdentityUnavailable(
                    "Staging JWKS temporarily unavailable"
                )
            return _jwks_cache
        _last_unknown_kid_refresh_at = now
        try:
            jwks = await _fetch_staging_jwks()
        except NeonStagingIdentityUnavailable:
            _jwks_failure_retry_at = time.monotonic() + _JWKS_FAILURE_RETRY_SECONDS
            raise
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            _jwks_failure_retry_at = time.monotonic() + _JWKS_FAILURE_RETRY_SECONDS
            raise NeonStagingIdentityUnavailable("Malformed staging JWKS")
        if not (1 <= len(jwks["keys"]) <= 16):
            _jwks_failure_retry_at = time.monotonic() + _JWKS_FAILURE_RETRY_SECONDS
            raise NeonStagingIdentityUnavailable("Malformed staging JWKS")
        _jwks_cache = jwks
        _jwks_cache_expires_at = time.monotonic() + _JWKS_CACHE_TTL_SECONDS
        _jwks_failure_retry_at = 0.0
        return jwks


def _reset_staging_jwks_cache_for_tests() -> None:
    """Clear process-local public-key state between offline tests."""
    global _jwks_cache, _jwks_cache_expires_at, _jwks_failure_retry_at
    global _last_unknown_kid_refresh_at
    _jwks_cache = None
    _jwks_cache_expires_at = 0.0
    _last_unknown_kid_refresh_at = 0.0
    _jwks_failure_retry_at = 0.0


def verify_signed_staging_jwt(
    token: str,
    *,
    jwks: object,
    now_seconds: int | None = None,
) -> dict[str, Any]:
    """Verify an offline JWT fixture using only public Ed25519 keys.

    No caller-supplied issuer, audience or algorithm is trusted. A valid token
    authenticates only an email-verified non-banned synthetic identity; it does
    not grant staff permissions or a tenant selection.
    """
    if not isinstance(token, str) or len(token) > _MAX_TOKEN_SIZE:
        raise NeonStagingTokenError("Invalid JWT length")
    parts = token.split(".")
    if len(parts) != 3:
        raise NeonStagingTokenError("Invalid JWT segments")
    header_b64, claims_b64, sig_b64 = parts
    header = _json_object(_decode_segment(header_b64))
    if header.get("alg") != "EdDSA" or header.get("typ", "JWT") != "JWT":
        raise NeonStagingTokenError("Unsupported JWT algorithm")
    kid = header.get("kid")
    if not isinstance(kid, str) or not (1 <= len(kid) <= 128):
        raise NeonStagingTokenError("Missing signing key identifier")
    public_key = _get_signing_key(jwks, kid)
    signature = _decode_segment(sig_b64)
    if len(signature) != 64:
        raise NeonStagingTokenError("Malformed Ed25519 signature")
    try:
        public_key.verify(
            signature,
            (header_b64 + "." + claims_b64).encode("ascii"),
        )
    except InvalidSignature as exc:
        raise NeonStagingTokenError("JWT signature invalid") from exc

    claims = _json_object(_decode_segment(claims_b64))
    current = int(time.time()) if now_seconds is None else now_seconds
    exp, iat = claims.get("exp"), claims.get("iat")
    if (
        not isinstance(exp, int)
        or isinstance(exp, bool)
        or not isinstance(iat, int)
        or isinstance(iat, bool)
        or exp <= current
        or iat > current + _ALLOWED_CLOCK_SKEW_SECONDS
        or exp <= iat
        or exp - iat > _MAX_JWT_LIFETIME_SECONDS
    ):
        raise NeonStagingTokenError("Invalid JWT lifetime")

    nbf = claims.get("nbf")
    if nbf is not None and (
        not isinstance(nbf, int)
        or isinstance(nbf, bool)
        or nbf > current + _ALLOWED_CLOCK_SKEW_SECONDS
    ):
        raise NeonStagingTokenError("Invalid JWT not-before")

    if claims.get("iss") != STAGING_AUTH_ORIGIN:
        raise NeonStagingTokenError("Unexpected staging issuer")
    if claims.get("aud") != STAGING_AUTH_ORIGIN:
        raise NeonStagingTokenError("Unexpected staging audience")
    sub = claims.get("sub")
    if (
        not isinstance(sub, str)
        or not (1 <= len(sub) <= 128)
        or claims.get("id") != sub
    ):
        raise NeonStagingTokenError("Invalid staging identity")
    email = claims.get("email")
    if (
        not isinstance(email, str)
        or not (3 <= len(email) <= 320)
        or "@" not in email
        or claims.get("emailVerified") is not True
        or claims.get("banned") is True
        or claims.get("role") != "authenticated"
    ):
        raise NeonStagingTokenError("Staging identity not eligible")
    return claims


async def verify_neon_staging_token(token: str) -> dict[str, Any]:
    """Fetch JWKS from one pinned Neon staging endpoint and verify the token.

    No redirects, proxy environment or arbitrary JWKS URLs are accepted. The
    response is size bounded and failures return a generic 503 to the caller.
    """
    kid = _prevalidate_token_header(token)
    jwks, fetched = await _cached_staging_jwks()
    if not _jwks_has_kid(jwks, kid) and not fetched:
        jwks = await _refresh_unknown_kid(kid)
    return verify_signed_staging_jwt(token, jwks=jwks)
