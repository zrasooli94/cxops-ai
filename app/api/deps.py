from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.auth import get_current_principal
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.principal import AuthenticatedPrincipal
from app.integrations.webhooks.security import verify_signature

log = get_logger(__name__)

WEBHOOK_SIGNATURE_HEADER = "x-cxops-signature"


async def get_current_principal_dep(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedPrincipal:
    """FastAPI dependency for the current authenticated principal.

    Delegates to app.core.auth.get_current_principal, which is the single
    authentication boundary. All token validation and HTTP error mapping
    (401 for missing/invalid credentials, 500 for auth misconfiguration)
    lives there.

    Example:
        @router.get("/protected")
        async def protected_endpoint(
            principal: CurrentPrincipal,
        ):
            ...

    Returns:
        AuthenticatedPrincipal: The verified principal from the JWT token.
    """
    return await get_current_principal(authorization=authorization)


CurrentPrincipal = Annotated[
    AuthenticatedPrincipal,
    Depends(get_current_principal_dep),
]


async def verify_ticket_event_signature(request: Request) -> bytes:
    """Machine-authentication dependency for the generic ticket-event webhook.

    Rejects the request unless it carries a valid HMAC-SHA256 signature over
    the raw request body in the ``X-CXOps-Signature`` header. This is machine
    authentication only — human JWT is neither required nor accepted here.

    Returns the raw request body on success.
    """
    secret = get_settings().ticket_event_webhook_secret

    if not secret:
        log.warning("webhook_secret_not_configured", route="ticket-events")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook not configured",
        )

    signature = request.headers.get(WEBHOOK_SIGNATURE_HEADER)

    if not signature:
        log.warning("webhook_signature_missing", route="ticket-events")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook signature",
        )

    body = await request.body()

    if not verify_signature(
        secret=secret,
        body=body,
        signature=signature,
    ):
        log.warning("webhook_signature_invalid", route="ticket-events")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    return body