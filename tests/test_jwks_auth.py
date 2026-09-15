# Dedicated JWKS verification tests for Phase 1B.2a.
#
# All key material is generated locally inside this module. NO test contacts a
# live identity provider; JWKS responses are mocked at the HTTP layer with
# respx. Tests exercise real RSA/JWT cryptography, not a mocked verifier.

import base64
import hashlib
import hmac
import json
import time
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from typing import ClassVar

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose.utils import base64url_decode

from app.api.deps import CurrentPrincipal
from app.core.auth import create_principal_from_payload
from app.core.config import get_settings, reset_settings_cache
from app.core.jwks import (
    JWKSAlgorithmError,
    JWKSFetchError,
    JWKSKeyNotFoundError,
    JWKSParseError,
    JWKSVerificationError,
    _clear_cache,
    _get_cache,
    verify_jwks_token,
)
from app.core.principal import AuthenticatedPrincipal

JWKS_URL = "https://auth.test.local/.well-known/jwks.json"
TEST_ISSUER = "test-issuer"
TEST_AUDIENCE = "test-audience"
TEST_SECRET = "y" * 32
KID_A = "kid-a"
KID_B = "kid-b"
KID_UNKNOWN = "kid-unknown"
KID_EC = "kid-ec"


def _configure(monkeypatch, **overrides) -> None:
    values = {
        "ENVIRONMENT": "development",
        "AUTH_MODE": "jwks",
        "AUTH_JWKS_URL": JWKS_URL,
        "AUTH_JWKS_ALGORITHMS": "RS256",
        "AUTH_JWKS_CACHE_TTL_SECONDS": "60",
        "AUTH_JWT_ISSUER": TEST_ISSUER,
        "AUTH_JWT_AUDIENCE": TEST_AUDIENCE,
        "AUTH_JWT_SECRET": TEST_SECRET,
        "AUTH_DEV_MODE": "False",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()


# ---------------------------------------------------------------- crypto utils


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_b64(i: int) -> str:
    length = (i.bit_length() + 7) // 8
    return _b64u(i.to_bytes(length, "big"))


def _generate_keypair() -> tuple[RSAPrivateKey, str, rsa.RSAPublicKey]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return private, pem, private.public_key()


_PRIV_A, _PEM_A, _PUB_A = _generate_keypair()
_PRIV_B, _PEM_B, _PUB_B = _generate_keypair()


def _public_jwk(public_key, kid: str) -> dict:
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _int_b64(numbers.n),
        "e": _int_b64(numbers.e),
    }


def _jwks_doc(*keys: dict) -> dict:
    return {"keys": list(keys)}


def _payload(
    sub: str = "user-123",
    email: str = "test@example.com",
    iss: str = TEST_ISSUER,
    aud: str = TEST_AUDIENCE,
    exp: int | None = None,
    include_sub: bool = True,
) -> dict:
    payload: dict = {}
    if include_sub:
        payload["sub"] = sub
    if email:
        payload["email"] = email
    if iss:
        payload["iss"] = iss
    if aud:
        payload["aud"] = aud
    payload["exp"] = exp or int(
        (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
    )
    return payload


def _rsa_hash(alg: str) -> hashes.HashAlgorithm:
    if alg == "RS384":
        return hashes.SHA384()
    if alg == "RS512":
        return hashes.SHA512()
    return hashes.SHA256()


def _sign_rsa(pem: str, header: dict, payload: dict, alg: str = "RS256") -> str:
    private = load_pem_private_key(pem.encode(), password=None)
    h = _b64u(json.dumps(header, separators=(",", ":")).encode())
    p = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    signature = private.sign(signing_input, padding.PKCS1v15(), _rsa_hash(alg))
    return f"{h}.{p}.{_b64u(signature)}"


def _sign_symmetric(secret: str, header: dict, payload: dict, alg: str) -> str:
    h = _b64u(json.dumps(header, separators=(",", ":")).encode())
    p = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    hash_fn = hashlib.sha512 if alg == "HS512" else hashlib.sha256
    digest = hmac.new(secret.encode(), f"{h}.{p}".encode(), hash_fn).digest()
    return f"{h}.{p}.{_b64u(digest)}"


def _rs256_token(
    key_pem: str = _PEM_A,
    kid: str = KID_A,
    **payload_overrides,
) -> str:
    return _sign_rsa(
        key_pem,
        {"alg": "RS256", "typ": "JWT", "kid": kid},
        _payload(**payload_overrides),
    )


def _tampered(token: str) -> str:
    header, payload_b64, signature = token.split(".")
    payload = json.loads(base64url_decode(payload_b64.encode()))
    payload["sub"] = "attacker-subject"
    tampered_payload = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    return f"{header}.{tampered_payload}.{signature}"


# ------------------------------------------------------------- HTTP app probe


probe_app = FastAPI(title="jwks-auth-probe")


@probe_app.get("/probe")
async def _auth_probe(principal: CurrentPrincipal):
    """Protected endpoint used to exercise the real auth dependency."""
    return {"subject": principal.subject, "email": principal.email}


async def _probe_request(token: str) -> httpx.Response:
    async with AsyncClient(
        transport=ASGITransport(app=probe_app), base_url="http://testserver"
    ) as client:
        return await client.get("/probe", headers={"Authorization": f"Bearer {token}"})


def _mock_jwks(doc: dict) -> respx.Route:
    return respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=doc))


@pytest.fixture(autouse=True)
def _default_jwks_env(monkeypatch):
    _configure(monkeypatch)
    yield


@pytest.fixture(autouse=True)
def _clean_jwks_cache():
    _clear_cache()
    yield
    _clear_cache()


# ------------------------------------------------------------- signature tests


class TestJWKSSignature:
    """Real RSA/RS256 cryptographic verification via verify_jwks_token."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_payload(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            payload = await verify_jwks_token(_rs256_token())
        assert payload["sub"] == "user-123"
        assert payload["email"] == "test@example.com"

    @pytest.mark.asyncio
    async def test_wrong_private_key_rejected(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            with pytest.raises(JWKSVerificationError):
                await verify_jwks_token(_rs256_token(key_pem=_PEM_B, kid=KID_A))

    @pytest.mark.asyncio
    async def test_tampered_payload_rejected(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            with pytest.raises(JWKSVerificationError):
                await verify_jwks_token(_tampered(_rs256_token()))

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            token = _rs256_token(
                exp=int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
            )
            with pytest.raises(JWKSVerificationError):
                await verify_jwks_token(token)

    @pytest.mark.asyncio
    async def test_missing_sub_rejected(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            with pytest.raises(JWKSVerificationError):
                await verify_jwks_token(_rs256_token(include_sub=False))

    @pytest.mark.asyncio
    async def test_empty_sub_rejected(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            with pytest.raises(JWKSVerificationError):
                await verify_jwks_token(_rs256_token(sub=""))

    @pytest.mark.asyncio
    async def test_wrong_issuer_rejected_when_configured(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            with pytest.raises(JWKSVerificationError):
                await verify_jwks_token(_rs256_token(iss="evil-issuer", aud=""))

    @pytest.mark.asyncio
    async def test_correct_issuer_accepted(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            payload = await verify_jwks_token(_rs256_token(aud=""))
        assert payload["iss"] == TEST_ISSUER

    @pytest.mark.asyncio
    async def test_issuer_optional_when_unconfigured(self, monkeypatch):
        _configure(monkeypatch, AUTH_JWT_ISSUER="")
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            payload = await verify_jwks_token(_rs256_token(iss="", aud=""))
        assert payload["sub"] == "user-123"

    @pytest.mark.asyncio
    async def test_wrong_audience_rejected_when_configured(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            with pytest.raises(JWKSVerificationError):
                await verify_jwks_token(_rs256_token(iss="", aud="evil-audience"))

    @pytest.mark.asyncio
    async def test_correct_audience_accepted(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            payload = await verify_jwks_token(_rs256_token())
        assert payload["aud"] == TEST_AUDIENCE

    @pytest.mark.asyncio
    async def test_audience_optional_when_unconfigured(self, monkeypatch):
        _configure(monkeypatch, AUTH_JWT_AUDIENCE="")
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            payload = await verify_jwks_token(_rs256_token())
        assert payload["sub"] == "user-123"


# ------------------------------------------------------------ algorithm tests


class TestJWKSAlgorithmConfusion:
    """Alg confusion is blocked before any JWKS interaction."""

    @pytest.mark.asyncio
    async def test_hs256_rejected_in_jwks_mode(self):
        token = _sign_symmetric(
            TEST_SECRET, {"alg": "HS256", "kid": KID_A}, _payload(), "HS256"
        )
        with pytest.raises(JWKSAlgorithmError):
            await verify_jwks_token(token)

    @pytest.mark.asyncio
    async def test_hs512_rejected_in_jwks_mode(self):
        token = _sign_symmetric(
            TEST_SECRET, {"alg": "HS512", "kid": KID_A}, _payload(), "HS512"
        )
        with pytest.raises(JWKSAlgorithmError):
            await verify_jwks_token(token)

    @pytest.mark.asyncio
    async def test_none_algorithm_rejected(self):
        token = _sign_rsa(
            _PEM_A, {"alg": "none", "typ": "JWT", "kid": KID_A}, _payload()
        )
        with pytest.raises(JWKSAlgorithmError):
            await verify_jwks_token(token)

    @pytest.mark.asyncio
    async def test_missing_alg_rejected(self):
        token = _sign_rsa(_PEM_A, {"typ": "JWT", "kid": KID_A}, _payload())
        with pytest.raises(JWKSAlgorithmError):
            await verify_jwks_token(token)

    @pytest.mark.asyncio
    async def test_unsupported_asymmetric_alg_rejected(self):
        header = {"alg": "ES256", "kid": KID_A}
        h = _b64u(json.dumps(header, separators=(",", ":")).encode())
        p = _b64u(json.dumps(_payload(), separators=(",", ":")).encode())
        token = f"{h}.{p}.{_b64u(b'dummy')}"
        with pytest.raises(JWKSAlgorithmError):
            await verify_jwks_token(token)

    @pytest.mark.asyncio
    async def test_allowlist_rejects_rs384(self):
        token = _sign_rsa(
            _PEM_A, {"alg": "RS384", "kid": KID_A}, _payload(), alg="RS384"
        )
        with pytest.raises(JWKSAlgorithmError):
            await verify_jwks_token(token)

    @pytest.mark.asyncio
    async def test_allowlist_allows_rs384(self, monkeypatch):
        _configure(monkeypatch, AUTH_JWKS_ALGORITHMS="RS256,RS384")
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            payload = await verify_jwks_token(
                _sign_rsa(
                    _PEM_A, {"alg": "RS384", "kid": KID_A}, _payload(), alg="RS384"
                )
            )
        assert payload["sub"] == "user-123"

    @pytest.mark.asyncio
    async def test_allowlist_cannot_re_enable_hs256_in_jwks(self, monkeypatch):
        _configure(monkeypatch, AUTH_JWKS_ALGORITHMS="RS256,HS256")
        token = _sign_symmetric(
            TEST_SECRET, {"alg": "HS256", "kid": KID_A}, _payload(), "HS256"
        )
        with pytest.raises(JWKSAlgorithmError):
            await verify_jwks_token(token)


# ------------------------------------------------------------- kid / jwks tests


class TestJWKSKidAndKeys:
    """kid header handling and JWKS document robustness."""

    @pytest.mark.asyncio
    async def test_missing_kid_rejected(self):
        doc = _jwks_doc(_public_jwk(_PUB_A, KID_A))
        with respx.mock:
            _mock_jwks(doc)
            token = _sign_rsa(_PEM_A, {"alg": "RS256", "typ": "JWT"}, _payload())
            with pytest.raises(JWKSAlgorithmError):
                await verify_jwks_token(token)

    @pytest.mark.asyncio
    async def test_unknown_kid_triggers_single_forced_refresh_then_rejected(self):
        doc = _jwks_doc(_public_jwk(_PUB_A, KID_A))
        with respx.mock:
            route = _mock_jwks(doc)
            first = await _probe_request(_rs256_token())
            assert first.status_code == 200
            assert route.call_count == 1

            unknown = _probe_request(_rs256_token(kid=KID_UNKNOWN))
            assert (await unknown).status_code == 401
            assert route.call_count == 3  # seed + refresh + forced refresh

    @pytest.mark.asyncio
    async def test_unknown_kid_discovered_on_forced_refresh(self):
        route = respx.get(JWKS_URL)
        route.side_effect = [
            httpx.Response(200, json=_jwks_doc(_public_jwk(_PUB_A, KID_A))),
            httpx.Response(
                200,
                json=_jwks_doc(_public_jwk(_PUB_A, KID_A), _public_jwk(_PUB_B, KID_B)),
            ),
        ]
        async with respx.mock:
            response = await _probe_request(_rs256_token(key_pem=_PEM_B, kid=KID_B))
            assert route.call_count == 2
        assert response.status_code == 200
        assert response.json()["subject"] == "user-123"

    @pytest.mark.asyncio
    async def test_malformed_jwks_json_raises_parse_error(self):
        with respx.mock:
            respx.get(JWKS_URL).mock(
                return_value=httpx.Response(
                    200,
                    content=b"not-json{",
                    headers={"content-type": "application/json"},
                )
            )
            with pytest.raises(JWKSParseError):
                await verify_jwks_token(_rs256_token())

    @pytest.mark.asyncio
    async def test_malformed_jwks_json_fails_closed_503(self):
        with respx.mock:
            respx.get(JWKS_URL).mock(
                return_value=httpx.Response(
                    200,
                    content=b"not-json{",
                    headers={"content-type": "application/json"},
                )
            )
            response = await _probe_request(_rs256_token())
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_jwks_missing_keys_field_fails_closed(self):
        with respx.mock:
            _mock_jwks({"status": "ok"})
            response = await _probe_request(_rs256_token())
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_keys_array_fails_closed(self):
        with respx.mock:
            _mock_jwks({"keys": []})
            response = await _probe_request(_rs256_token())
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_jwk_is_skipped_and_not_trusted(self):
        malformed = {"kty": "RSA", "kid": KID_B, "use": "sig", "n": "short-not-a-key"}
        doc = _jwks_doc(_public_jwk(_PUB_A, KID_A), malformed)
        with respx.mock:
            route = _mock_jwks(doc)
            bad = await _probe_request(_rs256_token(key_pem=_PEM_B, kid=KID_B))
            assert bad.status_code == 401
            assert route.call_count == 2
            _clear_cache()
            good = await _probe_request(_rs256_token())
            assert good.status_code == 200
            assert route.call_count == 3

    @pytest.mark.asyncio
    async def test_invalid_base64_jwk_is_skipped(self):
        malformed = {
            "kty": "RSA",
            "kid": KID_B,
            "use": "sig",
            "n": "not!!base64!!",
            "e": "AQAB",
        }
        with respx.mock:
            route = _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A), malformed))
            bad = await _probe_request(_rs256_token(key_pem=_PEM_B, kid=KID_B))
            assert bad.status_code == 401
            assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_non_rsa_jwk_rejected_when_rs256_configured(self):
        ec_jwk = {"kty": "EC", "kid": KID_EC, "crv": "P-256", "x": "x", "y": "y"}
        with respx.mock:
            _mock_jwks(_jwks_doc(ec_jwk))
            with pytest.raises(JWKSKeyNotFoundError):
                await verify_jwks_token(_rs256_token(kid=KID_EC))


# --------------------------------------------------------------- cache tests


def _cache_ttl() -> int:
    return get_settings().auth_jwks_cache_ttl_seconds


class TestJWKSCache:
    """Per-kid cache behavior and isolation."""

    @pytest.mark.asyncio
    async def test_first_valid_request_fetches_jwks(self):
        with respx.mock:
            route = _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            response = await _probe_request(_rs256_token())
            assert route.call_count == 1
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_second_request_same_kid_within_ttl_does_not_fetch(self):
        with respx.mock:
            route = _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            first = await _probe_request(_rs256_token())
            second = await _probe_request(_rs256_token())
            assert first.status_code == 200
            assert second.status_code == 200
            assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_expired_cache_triggers_refresh(self):
        with respx.mock:
            route = _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            await _probe_request(_rs256_token())
            assert route.call_count == 1
            key, _cached_at = _get_cache()[KID_A]
            _get_cache()[KID_A] = (key, time.time() - _cache_ttl() - 1)
            response = await _probe_request(_rs256_token())
            assert response.status_code == 200
            assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_fresh_cache_serves_during_provider_outage(self):
        with respx.mock:
            route = _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            first = await _probe_request(_rs256_token())
            assert first.status_code == 200
            assert route.call_count == 1
            route.mock(return_value=httpx.Response(503))
            second = await _probe_request(_rs256_token())
            assert second.status_code == 200
            assert route.call_count == 1  # cached key served; no second fetch

    @pytest.mark.asyncio
    async def test_expired_cache_with_provider_down_fails_closed(self):
        with respx.mock:
            route = _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            await _probe_request(_rs256_token())
            assert route.call_count == 1
            key, _cached_at = _get_cache()[KID_A]
            _get_cache()[KID_A] = (key, time.time() - _cache_ttl() - 1)
            route.mock(return_value=httpx.Response(503))
            response = await _probe_request(_rs256_token())
            assert route.call_count == 2
        assert response.status_code == 503

    def test_cache_is_cleared_between_tests(self):
        assert _get_cache() == {}


# ----------------------------------------------------------- rotation tests


class TestKeyRotation:
    """JWKS key rotation via the cache strategy."""

    @pytest.mark.asyncio
    async def test_new_key_discovered_after_rotation(self):
        old_doc = _jwks_doc(_public_jwk(_PUB_A, KID_A))
        new_doc = _jwks_doc(_public_jwk(_PUB_A, KID_A), _public_jwk(_PUB_B, KID_B))
        with respx.mock:
            route = _mock_jwks(old_doc)
            old = await _probe_request(_rs256_token())
            assert old.status_code == 200
            assert route.call_count == 1

            route.mock(return_value=httpx.Response(200, json=new_doc))
            new = await _probe_request(_rs256_token(key_pem=_PEM_B, kid=KID_B))
            assert new.status_code == 200
            assert route.call_count == 2  # exactly one fetch discovers the new key

            old_again = await _probe_request(_rs256_token())
            assert old_again.status_code == 200
            assert route.call_count == 2  # cached old key; no further fetch

    @pytest.mark.asyncio
    async def test_revoked_key_not_honored_after_ttl(self):
        with respx.mock:
            route = _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            first = await _probe_request(_rs256_token())
            assert first.status_code == 200
            assert route.call_count == 1
            key, _cached_at = _get_cache()[KID_A]
            _get_cache()[KID_A] = (key, time.time() - _cache_ttl() - 1)

            route.mock(
                return_value=httpx.Response(
                    200, json=_jwks_doc(_public_jwk(_PUB_B, KID_B))
                )
            )
            revoked = await _probe_request(_rs256_token())
            assert revoked.status_code == 401
            assert route.call_count == 3  # refresh + forced refresh both lack kid-a


# ----------------------------------------------------------- outage tests


class TestProviderOutage:
    """Fail-closed behavior when the JWKS provider is unavailable."""

    @pytest.mark.asyncio
    async def test_no_cached_key_provider_down_raises_fetch_error(self):
        with respx.mock:
            respx.get(JWKS_URL).mock(return_value=httpx.Response(503))
            with pytest.raises(JWKSFetchError):
                await verify_jwks_token(_rs256_token())

    @pytest.mark.asyncio
    async def test_no_cached_key_provider_down_503(self):
        with respx.mock:
            respx.get(JWKS_URL).mock(return_value=httpx.Response(503))
            response = await _probe_request(_rs256_token())
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_upstream_500_fails_closed_503(self):
        with respx.mock:
            respx.get(JWKS_URL).mock(return_value=httpx.Response(500))
            response = await _probe_request(_rs256_token())
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_connection_error_fails_closed_503(self):
        with respx.mock:
            respx.get(JWKS_URL).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            response = await _probe_request(_rs256_token())
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_jwks_failure_never_falls_back_to_hs256(self):
        with respx.mock:
            respx.get(JWKS_URL).mock(return_value=httpx.Response(503))
            response = await _probe_request(_rs256_token())
        assert response.status_code == 503
        assert "subject" not in response.text


# ------------------------------------------------------- error-semantics tests


class TestHTTPErrorSemantics:
    """Status mapping and information-leak hygiene at the probe endpoint."""

    CASES: ClassVar[frozenset[str]] = frozenset(
        {
            "wrong_key",
            "expired",
            "wrong_issuer",
            "wrong_audience",
            "unsupported_algorithm",
            "unknown_kid",
        }
    )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", sorted(CASES))
    async def test_rejection_mapping(self, case):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            _clear_cache()
            if case == "wrong_key":
                token = _rs256_token(key_pem=_PEM_B)
            elif case == "expired":
                token = _rs256_token(
                    exp=int(
                        (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
                    )
                )
            elif case == "wrong_issuer":
                token = _rs256_token(iss="evil-issuer")
            elif case == "wrong_audience":
                token = _rs256_token(aud="evil-audience")
            elif case == "unsupported_algorithm":
                token = _sign_symmetric(
                    TEST_SECRET, {"alg": "HS256", "kid": KID_A}, _payload(), "HS256"
                )
            elif case == "unknown_kid":
                token = _rs256_token(kid=KID_UNKNOWN)
            else:
                raise AssertionError(case)
            response = await _probe_request(token)
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_401_has_www_authenticate_bearer(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            response = await _probe_request(_rs256_token(key_pem=_PEM_B))
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    @pytest.mark.asyncio
    async def test_503_detail_is_generic(self):
        with respx.mock:
            respx.get(JWKS_URL).mock(return_value=httpx.Response(503))
            response = await _probe_request(_rs256_token())
        assert response.status_code == 503
        assert response.json()["detail"] == "Authentication service unavailable"
        assert JWKS_URL not in response.text
        assert "kid" not in response.text.lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", sorted(CASES))
    async def test_no_secrets_or_token_in_client_body(self, case):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            if case == "wrong_key":
                token = _rs256_token(key_pem=_PEM_B)
            elif case == "expired":
                token = _rs256_token(
                    exp=int(
                        (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
                    )
                )
            elif case == "wrong_issuer":
                token = _rs256_token(iss="evil-issuer")
            elif case == "wrong_audience":
                token = _rs256_token(aud="evil-audience")
            elif case == "unsupported_algorithm":
                token = _sign_symmetric(
                    TEST_SECRET, {"alg": "HS256", "kid": KID_A}, _payload(), "HS256"
                )
            elif case == "unknown_kid":
                token = _rs256_token(kid=KID_UNKNOWN)
            else:
                raise AssertionError(case)
            response = await _probe_request(token)
            assert response.status_code == 401
            assert token not in response.text
            assert TEST_SECRET not in response.text
            assert KID_A not in response.text


# ----------------------------------------------------------------- log tests


class _LogRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def _record(self, event: str, **kwargs) -> None:
        self.events.append({"event": event, **kwargs})

    def info(self, event: str, **kwargs) -> None:
        self._record(event, **kwargs)

    def warning(self, event: str, **kwargs) -> None:
        self._record(event, **kwargs)

    def error(self, event: str, **kwargs) -> None:
        self._record(event, **kwargs)


class TestJwksLogging:
    @pytest.mark.asyncio
    async def test_token_not_in_logs(self, monkeypatch):
        recorder = _LogRecorder()
        monkeypatch.setattr("app.core.auth.log", recorder)
        token = _rs256_token()
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            await _probe_request(token)
        for entry in recorder.events:
            assert token not in str(entry)

    @pytest.mark.asyncio
    async def test_authorization_header_not_in_logs(self, monkeypatch):
        recorder = _LogRecorder()
        monkeypatch.setattr("app.core.auth.log", recorder)
        token = _rs256_token()
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            await _probe_request(token)
        for entry in recorder.events:
            assert f"Bearer {token}" not in str(entry)

    @pytest.mark.asyncio
    async def test_private_key_material_not_in_logs(self, monkeypatch):
        recorder = _LogRecorder()
        monkeypatch.setattr("app.core.auth.log", recorder)
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            await _probe_request(_rs256_token(key_pem=_PEM_B))
        for entry in recorder.events:
            rendered = str(entry)
            assert _PEM_A not in rendered
            assert _PEM_B not in rendered

    @pytest.mark.asyncio
    async def test_private_jwk_params_not_in_logs(self, monkeypatch):
        recorder = _LogRecorder()
        monkeypatch.setattr("app.core.auth.log", recorder)
        private_marker = "PRIVATE_PARAM_MARKER_XYZ"
        leaked_jwk = {
            "kty": "RSA",
            "kid": "kid-private-params",
            "use": "sig",
            "n": _int_b64(_PUB_A.public_numbers().n),
            "e": "AQAB",
            "d": private_marker,
        }
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A), leaked_jwk))
            await _probe_request(_rs256_token())
        for entry in recorder.events:
            assert private_marker not in str(entry)

    @pytest.mark.asyncio
    async def test_configured_secrets_not_in_logs(self, monkeypatch):
        recorder = _LogRecorder()
        monkeypatch.setattr("app.core.auth.log", recorder)
        with respx.mock:
            respx.get(JWKS_URL).mock(return_value=httpx.Response(503))
            await _probe_request(_rs256_token())
        for entry in recorder.events:
            assert TEST_SECRET not in str(entry)


# ------------------------------------------------------ principal abstraction


class TestPrincipalAbstraction:
    def test_jwks_principal_auth_method(self):
        principal = create_principal_from_payload(
            {"sub": "user-123", "email": "test@example.com", "iss": TEST_ISSUER},
            auth_method="jwks",
        )
        assert isinstance(principal, AuthenticatedPrincipal)
        assert principal.auth_method == "jwks"
        assert principal.subject == "user-123"

    @pytest.mark.asyncio
    async def test_probe_returns_provider_neutral_fields(self):
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            response = await _probe_request(_rs256_token())
        assert response.status_code == 200
        assert set(response.json().keys()) == {"subject", "email"}
        assert "nhost" not in response.text.lower()

    def test_principal_has_no_nhost_specific_fields(self):
        names = {field.name for field in fields(AuthenticatedPrincipal)}
        assert names == {"subject", "email", "issuer", "auth_method", "token_claims"}
        assert not any("nhost" in name for name in names)


# ------------------------------------------------------------- production mode


class TestProductionMode:
    def test_production_hs256_rejected_at_configuration(self, monkeypatch):
        with pytest.raises(Exception) as excinfo:
            _configure(
                monkeypatch,
                ENVIRONMENT="production",
                AUTH_MODE="hs256",
                AUTH_JWT_SECRET=TEST_SECRET,
                AUTH_DEV_MODE="False",
            )
        assert "AUTH_MODE=hs256 is not allowed in production" in str(excinfo.value)

    def test_production_jwks_without_url_rejected(self, monkeypatch):
        with pytest.raises(Exception) as excinfo:
            _configure(
                monkeypatch,
                ENVIRONMENT="production",
                AUTH_MODE="jwks",
                AUTH_JWKS_URL="",
            )
        assert "AUTH_JWKS_URL is required" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_production_jwks_valid_configuration_accepted(self, monkeypatch):
        _configure(
            monkeypatch,
            ENVIRONMENT="production",
            AUTH_MODE="jwks",
            AUTH_JWKS_URL=JWKS_URL,
            AUTH_DEV_MODE="False",
        )
        with respx.mock:
            _mock_jwks(_jwks_doc(_public_jwk(_PUB_A, KID_A)))
            response = await _probe_request(_rs256_token())
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_jwks_failure_never_falls_back_to_hs256(self, monkeypatch):
        _configure(
            monkeypatch,
            ENVIRONMENT="production",
            AUTH_MODE="jwks",
            AUTH_JWKS_URL=JWKS_URL,
            AUTH_DEV_MODE="False",
        )
        with respx.mock:
            respx.get(JWKS_URL).mock(return_value=httpx.Response(503))
            response = await _probe_request(_rs256_token())
        assert response.status_code == 503
        assert "subject" not in response.text
