
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.principal import AuthenticatedPrincipal

log = get_logger(__name__)


class AuthenticationError(Exception):
    """Base authentication error."""


class InvalidTokenError(AuthenticationError):
    """Token is malformed or invalid."""


class ExpiredTokenError(AuthenticationError):
    """Token has expired."""


class InvalidClaimsError(AuthenticationError):
    """Token claims validation failed."""


class DevModeAuthenticationError(AuthenticationError):
    """Development authentication error."""


def _validate_dev_mode() -> None:
    """Ensure dev mode is not silently enabled in production."""
    s = get_settings()
    if s.auth_dev_mode and s.environment == "production":
        log.error("dev_mode_enabled_in_production")
        raise DevModeAuthenticationError(
            "AUTH_DEV_MODE cannot be enabled in production"
        )


def _get_jwt_secret() -> str:
    """Get JWT secret, with dev-mode fallback."""
    s = get_settings()
    if s.auth_dev_mode:
        return "dev-secret-not-for-production-use"
    if not s.auth_jwt_secret:
        raise InvalidTokenError("JWT secret not configured")
    return s.auth_jwt_secret


def _decode_and_validate_token(token: str) -> dict:
    """Decode and validate JWT token.

    Validates:
    - signature
    - expiration (exp)
    - issuer (iss) if configured
    - audience (aud) if configured
    - subject (sub) always required
    """
    s = get_settings()
    secret = _get_jwt_secret()

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[s.auth_jwt_algorithm],
            issuer=s.auth_jwt_issuer or None,
            audience=s.auth_jwt_audience or None,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": bool(s.auth_jwt_issuer),
                "verify_aud": bool(s.auth_jwt_audience),
            },
        )
    except ExpiredSignatureError as e:
        raise ExpiredTokenError("Token has expired") from e
    except JWTClaimsError as e:
        raise InvalidClaimsError(f"Invalid token claims: {e}") from e
    except JWTError as e:
        raise InvalidTokenError(f"Invalid token: {e}") from e

    # Explicitly validate required 'sub' claim
    if "sub" not in payload or not payload["sub"]:
        raise InvalidClaimsError("Token missing required 'sub' claim")

    return payload


def create_principal_from_payload(payload: dict) -> AuthenticatedPrincipal:
    """Create AuthenticatedPrincipal from validated JWT payload."""
    subject = payload.get("sub")
    if not subject:
        raise InvalidClaimsError("Token missing 'sub' claim")

    return AuthenticatedPrincipal(
        subject=str(subject),
        email=payload.get("email"),
        issuer=payload.get("iss"),
        auth_method="jwt",
        token_claims=payload,
    )


async def get_current_principal(authorization: str | None = None) -> AuthenticatedPrincipal:
    """FastAPI dependency: get current authenticated principal.

    Validates Authorization header Bearer token.
    Raises HTTPException with appropriate status on failure.
    """
    from fastapi import HTTPException, status

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        _validate_dev_mode()
        payload = _decode_and_validate_token(token)
    except ExpiredTokenError as e:
        log.warning("auth_expired_token", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidClaimsError as e:
        log.warning("auth_invalid_claims", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidTokenError as e:
        log.warning("auth_invalid_token", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except DevModeAuthenticationError as e:
        log.error("auth_dev_mode_production", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication misconfiguration",
        )

    principal = create_principal_from_payload(payload)

    log.info(
        "auth_success",
        subject=principal.subject,
        email=principal.email,
        issuer=principal.issuer,
    )

    return principal