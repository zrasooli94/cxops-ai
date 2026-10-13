"""Phase 1Q-R - identity evidence for invitation acceptance.

The Nhost userinfo verifier is exercised against an in-process fake of Nhost
Auth's ``GET /v1/user`` (httpx.MockTransport): no network, no real accounts.
"""

import httpx
import pytest

from app.core.config import get_settings, reset_settings_cache
from app.core.principal import AuthenticatedPrincipal
from app.services.identity_verification import (
    DisabledIdentityVerifier,
    IdentityVerificationError,
    IdentityVerificationUnavailableError,
    JwtClaimsIdentityVerifier,
    NhostUserInfoIdentityVerifier,
    build_identity_verifier,
)

SUB = "6a1f0c8e-2f0b-4b8e-9d3c-2b1d4f5a6c7e"
AUTH_URL = "https://fake-project.auth.local-test.nhost.run/v1"


def _principal(**claims) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(subject=SUB, token_claims={"sub": SUB, **claims})


def _verifier(handler) -> NhostUserInfoIdentityVerifier:
    return NhostUserInfoIdentityVerifier(AUTH_URL, transport=httpx.MockTransport(handler))


def _user(**overrides):
    user = {
        "id": SUB,
        "email": "Owner@Shop.Example",
        "emailVerified": True,
        "isAnonymous": False,
        "disabled": False,
    }
    user.update(overrides)
    return user


@pytest.mark.asyncio
async def test_userinfo_success_uses_callers_own_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_user())

    identity = await _verifier(handler).verify(_principal(), "caller-access-token")
    assert identity.email == "owner@shop.example"
    assert identity.subject == SUB
    assert seen == {"url": f"{AUTH_URL}/user", "auth": "Bearer caller-access-token"}


@pytest.mark.parametrize(
    ("user", "status"),
    [
        (_user(emailVerified=False), 403),  # unverified address
        (_user(email=None), 403),  # no email on the account
        (_user(id="someone-else"), 403),  # provider answered for another user
        (_user(isAnonymous=True), 403),  # anonymous Nhost session
        (_user(disabled=True), 403),  # disabled account
    ],
)
@pytest.mark.asyncio
async def test_userinfo_refuses_weak_or_mismatched_identity(user, status):
    verifier = _verifier(lambda _r: httpx.Response(200, json=user))
    with pytest.raises(IdentityVerificationError) as info:
        await verifier.verify(_principal(), "t")
    assert info.value.status_code == status


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401, json={}), IdentityVerificationError),
        (httpx.Response(500, json={}), IdentityVerificationUnavailableError),
        (httpx.Response(200, text="not json"), IdentityVerificationUnavailableError),
    ],
)
@pytest.mark.asyncio
async def test_userinfo_provider_failures_fail_closed(response, expected):
    verifier = _verifier(lambda _r: response)
    with pytest.raises(expected):
        await verifier.verify(_principal(), "t")


@pytest.mark.asyncio
async def test_userinfo_network_error_fails_closed():
    def boom(_request):
        raise httpx.ConnectError("unreachable")

    with pytest.raises(IdentityVerificationUnavailableError):
        await _verifier(boom).verify(_principal(), "t")


@pytest.mark.asyncio
async def test_jwt_claims_require_verified_email():
    verifier = JwtClaimsIdentityVerifier()
    with pytest.raises(IdentityVerificationError):
        await verifier.verify(_principal(), "t")  # no email claim
    with pytest.raises(IdentityVerificationError):
        await verifier.verify(_principal(email="a@b.example"), "t")  # unverified
    with pytest.raises(IdentityVerificationError):
        await verifier.verify(_principal(email="a@b.example", email_verified="no"), "t")
    ok = await verifier.verify(_principal(email="A@B.example", email_verified=True), "t")
    assert ok.email == "a@b.example"


@pytest.mark.asyncio
async def test_disabled_mode_is_the_default_and_fails_closed(monkeypatch):
    monkeypatch.delenv("INVITATION_IDENTITY_MODE", raising=False)
    reset_settings_cache()
    try:
        assert get_settings().invitation_identity_mode == "disabled"
        verifier = build_identity_verifier()
        assert isinstance(verifier, DisabledIdentityVerifier)
        with pytest.raises(IdentityVerificationUnavailableError):
            await verifier.verify(_principal(email="a@b.example", email_verified=True), "t")
    finally:
        reset_settings_cache()


def test_userinfo_mode_without_url_fails_closed(monkeypatch):
    monkeypatch.setenv("INVITATION_IDENTITY_MODE", "nhost_userinfo")
    monkeypatch.setenv("NHOST_AUTH_URL", "")
    reset_settings_cache()
    try:
        assert isinstance(build_identity_verifier(), DisabledIdentityVerifier)
    finally:
        reset_settings_cache()


def test_settings_reject_unknown_mode_and_plain_http(monkeypatch):
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(invitation_identity_mode="token_possession")
    with pytest.raises(ValidationError):
        Settings(nhost_auth_url="http://auth.example.com/v1")
    assert Settings(nhost_auth_url="http://localhost:1337/v1").nhost_auth_url


@pytest.mark.asyncio
async def test_userinfo_redirect_is_refused_and_not_followed():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/collect"})

    with pytest.raises(IdentityVerificationUnavailableError):
        await _verifier(handler).verify(_principal(), "t")
    assert calls == [f"{AUTH_URL}/user"]  # the token never reached the redirect target


def test_nhost_auth_url_rejects_credentials_query_and_fragment():
    from pydantic import ValidationError

    from app.core.config import Settings

    for bad in (
        "https://user:pw@auth.example.com/v1",
        "https://auth.example.com/v1?x=1",
        "https://auth.example.com/v1#frag",
        "ftp://auth.example.com/v1",
    ):
        with pytest.raises(ValidationError):
            Settings(nhost_auth_url=bad)
    assert Settings(nhost_auth_url="https://x.auth.ap-southeast-1.nhost.run/v1/").nhost_auth_url == (
        "https://x.auth.ap-southeast-1.nhost.run/v1"
    )
