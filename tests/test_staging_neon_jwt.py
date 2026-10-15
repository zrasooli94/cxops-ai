"""Synthetic-only adversarial JWT verification for Neon staging; zero network."""
import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from staging.neon_jwt import (
    STAGING_AUTH_ORIGIN,
    NeonStagingIdentityUnavailable,
    NeonStagingTokenError,
    verify_signed_staging_jwt,
)

NOW = 1_800_000_000
PRIVATE_KEY = Ed25519PrivateKey.generate()
PUBLIC_X = base64.urlsafe_b64encode(
    PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
).decode().rstrip("=")
KID = "synthetic-staging-key"
JWKS = {"keys": [
    {"kid": KID, "kty": "OKP", "crv": "Ed25519",
     "alg": "EdDSA", "use": "sig", "x": PUBLIC_X},
]}
BASE_CLAIMS = {
    "iat": NOW - 60,
    "exp": NOW + 840,
    "sub": "synthetic-staging-user-id",
    "id": "synthetic-staging-user-id",
    "email": "synthetic@example.test",
    "name": "Synthetic Staging Person",
    "emailVerified": True,
    "role": "authenticated",
    "banned": False,
    "iss": STAGING_AUTH_ORIGIN,
    "aud": STAGING_AUTH_ORIGIN,
}


def _encode_json(obj: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(obj, separators=(",", ":")).encode()
    ).decode().rstrip("=")


def _sign(
    claims: dict | None = None,
    *,
    header: dict | None = None,
    signer: Ed25519PrivateKey = PRIVATE_KEY,
) -> str:
    header_data = header if header is not None else {
        "alg": "EdDSA", "typ": "JWT", "kid": KID,
    }
    parts = ".".join([
        _encode_json(header_data), _encode_json(claims or BASE_CLAIMS)
    ])
    signature = signer.sign(parts.encode("ascii"))
    return parts + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")


def _verify(token: str, *, jwks: object = JWKS, now: int = NOW):
    return verify_signed_staging_jwt(token, jwks=jwks, now_seconds=now)


def test_valid_staging_identity_with_pinned_issuer_audience():
    actual = _verify(_sign())
    assert actual["sub"] == "synthetic-staging-user-id"
    assert actual["iss"] == STAGING_AUTH_ORIGIN


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("iss", "https://real-prod.example.test"),
        ("aud", "https://real-prod.example.test"),
        ("aud", ["https://real-prod.example.test", STAGING_AUTH_ORIGIN]),
        ("aud", None),
        ("sub", ""),
        ("id", "some-other-user"),
        ("email", "not-an-email"),
        ("emailVerified", False),
        ("emailVerified", None),
        ("role", "admin"),
        ("banned", True),
        ("exp", NOW),
        ("exp", NOW - 1),
        ("exp", NOW + 4000),
        ("exp", "1800000840"),
        ("exp", True),
        ("iat", NOW + 60),
        ("iat", "not-numeric"),
        ("nbf", NOW + 60),
    ],
)
def test_invalid_claims_never_grant_staging_access(field, wrong):
    claims = {**BASE_CLAIMS, field: wrong}
    with pytest.raises(NeonStagingTokenError):
        _verify(_sign(claims))


@pytest.mark.parametrize("algorithm", ["none", "HS256", "RS256", "ES256", None])
def test_algorithm_confusion_and_unsigned_headers_rejected(algorithm):
    with pytest.raises(NeonStagingTokenError):
        _verify(_sign(header={"alg": algorithm, "kid": KID, "typ": "JWT"}))


def test_tampered_payload_rejected_even_with_correct_key():
    original = _sign()
    header, _, sig = original.split(".")
    tampered = ".".join([header, _encode_json({**BASE_CLAIMS, "role": "owner"}), sig])
    with pytest.raises(NeonStagingTokenError):
        _verify(tampered)


def test_foreign_signing_key_and_kid_collision_rejected():
    different = Ed25519PrivateKey.generate()
    with pytest.raises(NeonStagingTokenError):
        _verify(_sign(signer=different))
    with pytest.raises(NeonStagingTokenError):
        _verify(_sign(header={"alg": "EdDSA", "kid": "unknown"}))
    with pytest.raises(NeonStagingTokenError):
        _verify(_sign(), jwks={"keys": [JWKS["keys"][0], JWKS["keys"][0]]})


@pytest.mark.parametrize(
    "invalid_key",
    [
        {"kid": KID, "kty": "RSA", "crv": "Ed25519", "x": PUBLIC_X},
        {"kid": KID, "kty": "OKP", "crv": "X25519", "x": PUBLIC_X},
        {"kid": KID, "kty": "OKP", "crv": "Ed25519", "alg": "RS256", "x": PUBLIC_X},
        {"kid": KID, "kty": "OKP", "crv": "Ed25519", "use": "enc", "x": PUBLIC_X},
        {"kid": KID, "kty": "OKP", "crv": "Ed25519", "key_ops": ["sign"], "x": PUBLIC_X},
        {"kid": KID, "kty": "OKP", "crv": "Ed25519", "x": "bad"},
    ],
)
def test_unexpected_jwk_types_rejected(invalid_key):
    with pytest.raises(NeonStagingTokenError):
        _verify(_sign(), jwks={"keys": [invalid_key]})


def test_malformed_jwks_fails_closed():
    for jwks in (None, [], {}, {"keys": []}, {"keys": "bad"}):
        with pytest.raises(NeonStagingIdentityUnavailable):
            _verify(_sign(), jwks=jwks)


def test_duplicate_header_fields_are_rejected():
    # Untrusted clients can submit a byte-level duplicate even when Python
    # dicts cannot hold duplicates. The signature itself is still valid.
    header = base64.urlsafe_b64encode(
        b'{"alg":"EdDSA","alg":"HS256","kid":"synthetic-staging-key"}'
    ).decode().rstrip("=")
    payload = _encode_json(BASE_CLAIMS)
    raw = f"{header}.{payload}"
    token = raw + "." + base64.urlsafe_b64encode(
        PRIVATE_KEY.sign(raw.encode())
    ).decode().rstrip("=")
    with pytest.raises(NeonStagingTokenError):
        _verify(token)


@pytest.mark.parametrize(
    "token",
    ["", "abc", "abc.def", "abc.def.ghi.jkl", "!" * 99, "a" * 8193],
)
def test_malformed_token_structure_fails_closed(token):
    with pytest.raises(NeonStagingTokenError):
        _verify(token)


def test_maximum_jwt_lifetime_and_timestamp_boundaries():
    assert _verify(_sign(), now=NOW)["sub"]
    with pytest.raises(NeonStagingTokenError):
        _verify(_sign(), now=NOW + 840)
    claims = {**BASE_CLAIMS, "iat": NOW - 4000, "exp": NOW + 60}
    with pytest.raises(NeonStagingTokenError):
        _verify(_sign(claims))


def test_verified_token_does_not_embed_cxops_tenant_authorization():
    actual = _verify(_sign())
    assert "organization_id" not in actual
    assert "permissions" not in actual
    assert "is_staff" not in actual
