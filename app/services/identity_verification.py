"""Verified-identity evidence for invitation acceptance (Phase 1Q-R).

An invitation is addressed to an email address; the token alone only proves
that someone *holds the link*. Binding a membership to the intended recipient
therefore needs trusted evidence that the authenticated Nhost user owns that
address and has verified it. Nhost access tokens do **not** carry an email
claim by default (they carry ``sub`` plus Hasura claims), so the evidence has
to come from one of these server-side sources, chosen by
``settings.invitation_identity_mode``:

``disabled`` (default)
    No source is configured: acceptance fails closed (HTTP 503). This is the
    production state until an owner approves one of the modes below.

``nhost_userinfo``
    The API calls Nhost Auth's ``GET {NHOST_AUTH_URL}/user`` with the caller's
    own, already-verified access token (no admin secret involved). The user
    record must have the same ``id`` as the token's ``sub``, ``emailVerified``
    true, ``isAnonymous`` false and ``disabled`` false.

``jwt_claims``
    For an Nhost project configured to sign custom claims into the access
    token: the verified token must carry both ``email`` and
    ``email_verified`` = true. A token with only ``email`` is rejected.

Every failure - missing evidence, unverified email, provider error, timeout -
is a refusal; nothing falls back to "token possession is enough".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.principal import AuthenticatedPrincipal
from app.services.commercial_common import normalize_email

log = get_logger(__name__)

IDENTITY_MODES = frozenset({"disabled", "nhost_userinfo", "jwt_claims"})


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    email: str  # normalized
    source: str


class IdentityVerificationError(Exception):
    """Base error; ``status_code`` is the HTTP mapping."""

    status_code = 403


class IdentityVerificationUnavailableError(IdentityVerificationError):
    status_code = 503


class IdentityVerifier(Protocol):
    async def verify(
        self, principal: AuthenticatedPrincipal, access_token: str
    ) -> VerifiedIdentity: ...


def _claim_true(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


class DisabledIdentityVerifier:
    async def verify(
        self, principal: AuthenticatedPrincipal, access_token: str
    ) -> VerifiedIdentity:
        raise IdentityVerificationUnavailableError(
            "Accepting invitations is not available yet. Contact your CXOps representative."
        )


class JwtClaimsIdentityVerifier:
    async def verify(
        self, principal: AuthenticatedPrincipal, access_token: str
    ) -> VerifiedIdentity:
        claims = principal.token_claims or {}
        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            raise IdentityVerificationError(
                "Your sign-in does not include an email address, so this invitation "
                "cannot be matched to you."
            )
        if not _claim_true(claims.get("email_verified")):
            raise IdentityVerificationError(
                "Verify your email address before accepting this invitation."
            )
        return VerifiedIdentity(
            subject=principal.subject, email=normalize_email(email), source="jwt_claims"
        )


class NhostUserInfoIdentityVerifier:
    def __init__(
        self,
        auth_url: str,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = auth_url.rstrip("/") + "/user"
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 2.0))
        self._transport = transport

    async def verify(
        self, principal: AuthenticatedPrincipal, access_token: str
    ) -> VerifiedIdentity:
        try:
            # Never follow redirects: the caller's access token must only ever
            # be sent to the configured Nhost Auth URL.
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = await client.get(
                    self._url, headers={"Authorization": f"Bearer {access_token}"}
                )
        except httpx.HTTPError as exc:
            log.warning("identity_userinfo_unreachable", error_type=type(exc).__name__)
            raise IdentityVerificationUnavailableError(
                "We could not verify your account right now. Please try again shortly."
            ) from exc
        if response.status_code in (401, 403):
            raise IdentityVerificationError("Your sign-in could not be verified. Sign in again.")
        if response.status_code != 200:
            log.warning("identity_userinfo_failed", status_code=response.status_code)
            raise IdentityVerificationUnavailableError(
                "We could not verify your account right now. Please try again shortly."
            )
        try:
            user = response.json()
        except ValueError as exc:
            raise IdentityVerificationUnavailableError(
                "We could not verify your account right now. Please try again shortly."
            ) from exc
        if not isinstance(user, dict) or str(user.get("id") or "") != principal.subject:
            # The provider answered for a different user than the token's sub:
            # never trust it.
            log.warning("identity_userinfo_subject_mismatch")
            raise IdentityVerificationError("Your sign-in could not be verified. Sign in again.")
        if user.get("isAnonymous") is True or user.get("disabled") is True:
            raise IdentityVerificationError("This account cannot accept invitations.")
        email = user.get("email")
        if not isinstance(email, str) or not email.strip():
            raise IdentityVerificationError(
                "Your account has no email address, so this invitation cannot be matched to you."
            )
        if user.get("emailVerified") is not True:
            raise IdentityVerificationError(
                "Verify your email address before accepting this invitation."
            )
        return VerifiedIdentity(
            subject=principal.subject, email=normalize_email(email), source="nhost_userinfo"
        )


def build_identity_verifier() -> IdentityVerifier:
    settings = get_settings()
    mode = settings.invitation_identity_mode
    if mode == "nhost_userinfo":
        if not settings.nhost_auth_url:
            # Misconfiguration: fail closed rather than silently disabling the check.
            return DisabledIdentityVerifier()
        return NhostUserInfoIdentityVerifier(
            settings.nhost_auth_url,
            timeout_seconds=settings.invitation_identity_timeout_seconds,
        )
    if mode == "jwt_claims":
        return JwtClaimsIdentityVerifier()
    return DisabledIdentityVerifier()
