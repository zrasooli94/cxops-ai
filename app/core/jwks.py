"""Provider-neutral JWKS verification adapter.

Supports generic RSA JWKS verification compatible with any OIDC/JWKS provider
that publishes a standard JWKS endpoint (e.g., Nhost with asymmetric signing,
Auth0, Keycloak, etc.).
"""

import binascii
import time

import httpx
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError
from jose.utils import base64url_decode

from app.core.config import get_settings


class JWKSError(Exception):
    """Base JWKS verification error."""


class JWKSFetchError(JWKSError):
    """JWKS endpoint fetch failed."""


class JWKSParseError(JWKSError):
    """JWKS response parse/validation failed."""


class JWKSKeyNotFoundError(JWKSError):
    """Requested kid not found in JWKS."""


class JWKSAlgorithmError(JWKSError):
    """Unsupported or mismatched algorithm."""


class JWKSVerificationError(JWKSError):
    """JWT signature/claims verification failed."""


# In-memory JWKS cache: {kid: (public_key, cached_at)}
_jwks_cache: dict[str, tuple] = {}
_cache_ttl_seconds: int = 600


def _get_cache_ttl() -> int:
    return get_settings().auth_jwks_cache_ttl_seconds


def _get_jwks_url() -> str:
    return get_settings().auth_jwks_url


def _get_allowed_algorithms() -> list[str]:
    s = get_settings()
    return [alg.strip() for alg in s.auth_jwks_algorithms.split(",") if alg.strip()]


def _is_allowed_algorithm(alg: str) -> bool:
    return alg in _get_allowed_algorithms()


def _clear_cache() -> None:
    """Clear the JWKS cache. Used for testing."""
    _jwks_cache.clear()


def _get_cache() -> dict:
    return _jwks_cache


async def _fetch_jwks() -> dict:
    """Fetch JWKS from the configured endpoint.

    Returns the parsed JSON dict with 'keys' list.
    """
    url = _get_jwks_url()
    if not url:
        raise JWKSFetchError("JWKS URL not configured")

    timeout = httpx.Timeout(5.0, connect=2.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        raise JWKSFetchError(f"JWKS fetch failed: {e.response.status_code}") from e
    except httpx.RequestError as e:
        raise JWKSFetchError(f"JWKS request failed: {e}") from e
    except ValueError as e:
        raise JWKSParseError(f"JWKS response not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise JWKSParseError("JWKS response is not a JSON object")
    if not isinstance(data.get("keys", []), list):
        raise JWKSParseError("JWKS 'keys' must be a list")
    return data


def _parse_jwk_to_public_key(jwk: dict):
    """Parse a JWK dict to a cryptography public key object."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    if jwk.get("kty") != "RSA":
        raise JWKSParseError(f"Unsupported key type: {jwk.get('kty')}")

    if jwk.get("use") and jwk["use"] != "sig":
        raise JWKSParseError(f"Key not for signature use: {jwk.get('use')}")

    n_b64 = jwk.get("n")
    e_b64 = jwk.get("e")
    if (
        not isinstance(n_b64, str)
        or not isinstance(e_b64, str)
        or not n_b64
        or not e_b64
    ):
        raise JWKSParseError("JWK missing n or e")

    try:
        n = int.from_bytes(base64url_decode(n_b64.encode()), "big")
        e = int.from_bytes(base64url_decode(e_b64.encode()), "big")
    except (binascii.Error, ValueError) as exc:
        raise JWKSParseError("JWK contains invalid base64 parameters") from exc

    try:
        public_numbers = rsa.RSAPublicNumbers(e, n)
        return public_numbers.public_key()
    except ValueError as exc:
        raise JWKSParseError("JWK contains invalid RSA parameter values") from exc


async def _get_signing_key(kid: str):
    """Get the public key for a given kid, fetching JWKS if needed."""
    global _jwks_cache
    now = time.time()
    ttl = _get_cache_ttl()

    # Check cache first - _jwks_cache declared global at function entry
    if kid in _jwks_cache:
        key, cached_at = _jwks_cache[kid]
        if now - cached_at < ttl:
            return key
        # Expired entry falls through to a fresh fetch. No stale-grace: a key
        # past TTL is never used, and a failed fetch fails closed (caller).

    # Not in cache or expired - fetch fresh JWKS
    jwks = await _fetch_jwks()
    keys = jwks.get("keys", [])

    # Update entire cache
    _jwks_cache = {}
    for jwk in keys:
        jwk_kid = jwk.get("kid")
        if jwk_kid:
            try:
                key = _parse_jwk_to_public_key(jwk)
                _jwks_cache[jwk_kid] = (key, now)
            except JWKSParseError:
                # Skip malformed keys
                continue

    if kid not in _jwks_cache:
        raise JWKSKeyNotFoundError(f"Key '{kid}' not found in JWKS")

    return _jwks_cache[kid][0]


async def verify_jwks_token(token: str) -> dict:
    """Verify a JWT using JWKS asymmetric verification.

    Validates:
    - algorithm is allowed (RS256 by default)
    - kid header present
    - signature via JWKS
    - expiration (exp)
    - subject (sub) required
    - issuer (iss) if configured
    - audience (aud) if configured

    Returns validated payload dict.
    """
    s = get_settings()

    # Get unverified header to inspect alg and kid
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise JWKSVerificationError(f"Invalid token header: {e}") from e

    alg = header.get("alg")
    kid = header.get("kid")

    if not alg:
        raise JWKSAlgorithmError("Missing 'alg' header")
    if not _is_allowed_algorithm(alg):
        raise JWKSAlgorithmError(f"Algorithm '{alg}' not allowed")
    if alg == "HS256":
        raise JWKSAlgorithmError("HS256 not allowed in JWKS mode")
    if not kid:
        raise JWKSAlgorithmError("Missing 'kid' header")

    # Get signing key
    try:
        key = await _get_signing_key(kid)
    except JWKSKeyNotFoundError:
        # Force one refresh and retry
        try:
            jwks = await _fetch_jwks()
            keys = jwks.get("keys", [])
            now = time.time()
            global _jwks_cache
            _jwks_cache = {}
            for jwk in keys:
                jwk_kid = jwk.get("kid")
                if jwk_kid:
                    try:
                        k = _parse_jwk_to_public_key(jwk)
                        _jwks_cache[jwk_kid] = (k, now)
                    except JWKSParseError:
                        continue
            if kid not in _jwks_cache:
                raise JWKSKeyNotFoundError(f"Key '{kid}' not found after refresh")
            key = _jwks_cache[kid][0]
        except JWKSFetchError as e:
            raise JWKSFetchError(
                "JWKS unavailable and no trustworthy cached key"
            ) from e

    return await _verify_with_key(token, key, s)


async def _verify_with_key(token: str, key, settings) -> dict:
    """Verify token with a specific public key."""
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=_get_allowed_algorithms(),
            issuer=settings.auth_jwt_issuer or None,
            audience=settings.auth_jwt_audience or None,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": bool(settings.auth_jwt_issuer),
                "verify_aud": bool(settings.auth_jwt_audience),
            },
        )
    except ExpiredSignatureError as e:
        raise JWKSVerificationError("Token has expired") from e
    except JWTClaimsError as e:
        raise JWKSVerificationError(f"Invalid token claims: {e}") from e
    except JWTError as e:
        raise JWKSVerificationError(f"Invalid token: {e}") from e

    if "sub" not in payload or not payload["sub"]:
        raise JWKSVerificationError("Token missing required 'sub' claim")

    return payload


async def verify_hs256_token(token: str) -> dict:
    """Verify a JWT using local HS256 symmetric secret.

    Used only in development/test mode.
    """
    from app.core.auth import (
        _decode_and_validate_token,
        _validate_auth_mode,
    )

    _validate_auth_mode()
    return _decode_and_validate_token(token)