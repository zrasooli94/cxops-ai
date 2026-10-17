"""Synthetic-only adversarial JWT verification for Neon staging; zero network."""

import base64
import json
import time

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from staging.neon_jwt import (
    STAGING_AUTH_ORIGIN,
    STAGING_JWKS_URL,
    NeonStagingIdentityUnavailable,
    NeonStagingTokenError,
    _reset_staging_jwks_cache_for_tests,
    verify_neon_staging_token,
    verify_signed_staging_jwt,
)

NOW = 1_800_000_000
PRIVATE_KEY = Ed25519PrivateKey.generate()
PUBLIC_X = (
    base64.urlsafe_b64encode(
        PRIVATE_KEY.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    .decode()
    .rstrip("=")
)
KID = "synthetic-staging-key"
JWKS = {
    "keys": [
        {
            "kid": KID,
            "kty": "OKP",
            "crv": "Ed25519",
            "alg": "EdDSA",
            "use": "sig",
            "x": PUBLIC_X,
        },
    ]
}
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


@pytest.fixture(autouse=True)
def _isolated_jwks_cache():
    _reset_staging_jwks_cache_for_tests()
    yield
    _reset_staging_jwks_cache_for_tests()


def _encode_json(obj: dict) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _sign(
    claims: dict | None = None,
    *,
    header: dict | None = None,
    signer: Ed25519PrivateKey = PRIVATE_KEY,
) -> str:
    header_data = (
        header
        if header is not None
        else {
            "alg": "EdDSA",
            "typ": "JWT",
            "kid": KID,
        }
    )
    parts = ".".join([_encode_json(header_data), _encode_json(claims or BASE_CLAIMS)])
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
        {
            "kid": KID,
            "kty": "OKP",
            "crv": "Ed25519",
            "key_ops": ["sign"],
            "x": PUBLIC_X,
        },
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
    header = (
        base64.urlsafe_b64encode(
            b'{"alg":"EdDSA","alg":"HS256","kid":"synthetic-staging-key"}'
        )
        .decode()
        .rstrip("=")
    )
    payload = _encode_json(BASE_CLAIMS)
    raw = f"{header}.{payload}"
    token = (
        raw
        + "."
        + base64.urlsafe_b64encode(PRIVATE_KEY.sign(raw.encode())).decode().rstrip("=")
    )
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


@pytest.mark.asyncio
async def test_jwks_adapter_uses_exact_pinned_staging_endpoint_without_live_network():
    with respx.mock(assert_all_called=True) as router:
        pinned = router.get(STAGING_JWKS_URL).mock(
            return_value=httpx.Response(200, json=JWKS)
        )
        actual = await verify_neon_staging_token(
            _sign(
                {
                    **BASE_CLAIMS,
                    "iat": int(time.time()) - 30,
                    "exp": int(__import__("time").time()) + 870,
                }
            )
        )
        assert pinned.called
        assert actual["sub"] == "synthetic-staging-user-id"


@pytest.mark.asyncio
async def test_malformed_staging_token_never_triggers_external_auth_request():
    with respx.mock(assert_all_called=False) as router:
        probe = router.get(STAGING_JWKS_URL).mock(
            return_value=httpx.Response(200, json=JWKS)
        )
        with pytest.raises(NeonStagingTokenError):
            await verify_neon_staging_token("malformed")
        assert not probe.called


@pytest.mark.asyncio
async def test_shaped_but_invalid_tokens_never_trigger_external_auth_request():
    with respx.mock(assert_all_called=False) as router:
        probe = router.get(STAGING_JWKS_URL).mock(
            return_value=httpx.Response(200, json=JWKS)
        )
        for token in (
            "abc.def.ghi",
            _sign(header={"alg": "HS256", "kid": KID, "typ": "JWT"}),
            _sign(header={"alg": "EdDSA", "typ": "JWT"}),
        ):
            with pytest.raises(NeonStagingTokenError):
                await verify_neon_staging_token(token)
        assert not probe.called


@pytest.mark.asyncio
async def test_jwks_cache_and_single_flight_bound_outbound_requests():
    claims = {
        **BASE_CLAIMS,
        "iat": int(time.time()) - 30,
        "exp": int(time.time()) + 600,
    }
    token = _sign(claims)
    with respx.mock(assert_all_called=True) as router:
        probe = router.get(STAGING_JWKS_URL).mock(
            return_value=httpx.Response(200, json=JWKS)
        )
        results = await __import__("asyncio").gather(
            *[verify_neon_staging_token(token) for _ in range(20)]
        )
        assert all(result["sub"] == BASE_CLAIMS["sub"] for result in results)
        assert probe.call_count == 1


@pytest.mark.asyncio
async def test_unknown_kid_refresh_is_rate_limited_and_supports_rotation():
    now = int(time.time())
    first_token = _sign({**BASE_CLAIMS, "iat": now - 30, "exp": now + 600})
    rotated_key = Ed25519PrivateKey.generate()
    rotated_x = (
        base64.urlsafe_b64encode(
            rotated_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        .decode()
        .rstrip("=")
    )
    rotated_kid = "rotated-staging-key"
    rotated_jwks = {
        "keys": [
            {
                "kid": rotated_kid,
                "kty": "OKP",
                "crv": "Ed25519",
                "alg": "EdDSA",
                "use": "sig",
                "x": rotated_x,
            }
        ]
    }
    rotated_token = _sign(
        {**BASE_CLAIMS, "iat": now - 30, "exp": now + 600},
        header={"alg": "EdDSA", "typ": "JWT", "kid": rotated_kid},
        signer=rotated_key,
    )
    unknown = _sign(
        {**BASE_CLAIMS, "iat": now - 30, "exp": now + 600},
        header={"alg": "EdDSA", "typ": "JWT", "kid": "attacker-key"},
    )
    with respx.mock(assert_all_called=True) as router:
        probe = router.get(STAGING_JWKS_URL).mock(
            side_effect=[
                httpx.Response(200, json=JWKS),
                httpx.Response(200, json=rotated_jwks),
            ]
        )
        assert (await verify_neon_staging_token(first_token))["sub"]
        assert (await verify_neon_staging_token(rotated_token))["sub"]
        for _ in range(10):
            with pytest.raises(NeonStagingTokenError):
                await verify_neon_staging_token(unknown)
        assert probe.call_count == 2


@pytest.mark.asyncio
async def test_jwks_network_errors_and_oversized_responses_fail_closed():
    with respx.mock(assert_all_called=True) as router:
        router.get(STAGING_JWKS_URL).mock(
            return_value=httpx.Response(200, content=b"x" * 32769)
        )
        with pytest.raises(NeonStagingIdentityUnavailable):
            await verify_neon_staging_token(_sign())


@pytest.mark.asyncio
async def test_jwks_outage_backoff_bounds_concurrent_and_repeated_requests():
    token = _sign(
        {
            **BASE_CLAIMS,
            "iat": int(time.time()) - 30,
            "exp": int(time.time()) + 600,
        }
    )
    with respx.mock(assert_all_called=True) as router:
        probe = router.get(STAGING_JWKS_URL).mock(return_value=httpx.Response(503))
        results = await __import__("asyncio").gather(
            *(verify_neon_staging_token(token) for _ in range(20)),
            return_exceptions=True,
        )
        assert all(isinstance(item, NeonStagingIdentityUnavailable) for item in results)
        for _ in range(10):
            with pytest.raises(NeonStagingIdentityUnavailable):
                await verify_neon_staging_token(token)
        assert probe.call_count == 1
