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
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class NeonStagingTokenError(Exception):
    """Invalid staging token (client receives a generic 401)."""


class NeonStagingIdentityUnavailable(Exception):
    """Pinned staging JWKS temporarily unavailable (client receives 503)."""


def _decode_segment(segment: str) -> bytes:
    if not segment or len(segment) > _MAX_TOKEN_SIZE or not _BASE64URL_RE.fullmatch(segment):
        raise NeonStagingTokenError("Invalid JWT encoding")
    try:
        return base64.urlsafe_b64decode(
            segment + "=" * (-len(segment) % 4)
        )
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
    candidates = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid]
    if len(candidates) != 1:
        raise NeonStagingTokenError("No unique staging signing key")
    key = candidates[0]
    if (
        key.get("kty") != "OKP"
        or key.get("crv") != "Ed25519"
        or key.get("alg", "EdDSA") != "EdDSA"
        or key.get("use", "sig") != "sig"
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
        not isinstance(exp, int) or isinstance(exp, bool)
        or not isinstance(iat, int) or isinstance(iat, bool)
        or exp <= current or iat > current + _ALLOWED_CLOCK_SKEW_SECONDS
        or exp <= iat or exp - iat > _MAX_JWT_LIFETIME_SECONDS
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
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(3.0, connect=1.5),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(
                STAGING_JWKS_URL, headers={"Accept": "application/json"}
            )
            if response.status_code != 200 or len(response.content) > _MAX_JWKS_BYTES:
                raise NeonStagingIdentityUnavailable("Staging JWKS unavailable")
            jwks = response.json()
    except (httpx.RequestError, ValueError) as exc:
        raise NeonStagingIdentityUnavailable("Staging JWKS unavailable") from exc
    return verify_signed_staging_jwt(token, jwks=jwks)
