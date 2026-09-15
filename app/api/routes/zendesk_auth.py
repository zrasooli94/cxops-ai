import hmac
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant
from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.services.zendesk_oauth_service import (
    ZendeskOAuthError,
    ZendeskOAuthService,
    ZendeskOAuthStateError,
)

router = APIRouter(
    prefix="/auth/zendesk",
    tags=["Zendesk OAuth"],
)

log = get_logger(__name__)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

STATE_COOKIE_NAME = "zendesk_oauth_state"


@router.get(
    "/login",
    status_code=status.HTTP_302_FOUND,
)
async def zendesk_login(
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    db: DatabaseSession,
):
    """Tenant-bound OAuth initiation.

    Requires an authenticated principal with a resolved tenant. The state is
    durably bound to ``tenant.organization_id`` and the initiating subject; the
    browser only ever sees the unguessable nonce.
    """
    state = await ZendeskOAuthService.create_state(
        db=db,
        organization_id=tenant.organization_id,
        subject=principal.subject,
    )

    authorization_url = ZendeskOAuthService.build_authorization_url(state)

    response = RedirectResponse(
        authorization_url,
        status_code=status.HTTP_302_FOUND,
    )

    response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=state,
        httponly=True,
        samesite="lax",
        max_age=get_settings().zendesk_oauth_state_ttl_seconds,
    )

    return response


@router.get("/callback")
async def zendesk_callback(
    request: Request,
    db: DatabaseSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """OAuth callback. Publicly reachable (Zendesk redirects the user's
    browser here), but the authorization decision is NOT made in the browser.

    Authorization flow:
        1. ``state`` must match the HttpOnly cookie nonce (CSRF / cross-device
           binding).
        2. The durable state record is consumed exactly once and must not be
           expired. Its persisted organization_id provides the tenant.
        3. The authorization code is exchanged and the credential is stored
           under THAT organization only. The browser never supplies an
           organization, subject, or credential.
    """

    if error:
        log.warning(
            "zendesk_oauth_provider_error",
            error_category="provider_denied",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Zendesk authorization failed.",
        )

    expected_state = request.cookies.get(STATE_COOKIE_NAME)

    if (
        not state
        or not expected_state
        or not hmac.compare_digest(
            state,
            expected_state,
        )
    ):
        log.warning(
            "zendesk_oauth_state_mismatch",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state.",
        )

    try:
        bound_state = await ZendeskOAuthService.consume_state(
            db=db,
            state=state,
        )
    except ZendeskOAuthStateError as exc:
        log.warning(
            "zendesk_oauth_state_invalid",
            error_category=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state.",
        )

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code.",
        )

    try:
        token = await ZendeskOAuthService.exchange_code(
            db=db,
            code=code,
            organization_id=bound_state.organization_id,
            subject=bound_state.subject,
        )

    except ZendeskOAuthError:
        log.warning(
            "zendesk_oauth_exchange_failed",
            organization_id=bound_state.organization_id,
            error_category="provider_exchange_failed",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Zendesk authorization failed.",
        )

    # Never echo access/refresh tokens. Only connection identity + scope.
    return {
        "status": "connected",
        "provider": "zendesk",
        "organization_id": token.organization_id,
        "integration_id": token.integration_id,
        "scope": token.scope,
        "expires_at": token.expires_at,
    }
