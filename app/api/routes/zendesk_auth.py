import hmac
import secrets
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

from app.core.database import get_db
from app.services.zendesk_oauth_service import (
    ZendeskOAuthError,
    ZendeskOAuthService,
)


router = APIRouter(
    prefix="/auth/zendesk",
    tags=["Zendesk OAuth"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


@router.get("/login")
async def zendesk_login():

    state = secrets.token_urlsafe(32)

    authorization_url = (
        ZendeskOAuthService.build_authorization_url(
            state
        )
    )

    response = RedirectResponse(
        authorization_url
    )

    response.set_cookie(
        key="zendesk_oauth_state",
        value=state,
        httponly=True,
        samesite="lax",
        max_age=600,
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

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Zendesk authorization failed: {error}",
        )

    expected_state = request.cookies.get(
        "zendesk_oauth_state"
    )

    if (
        not state
        or not expected_state
        or not hmac.compare_digest(
            state,
            expected_state,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state",
        )

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code",
        )

    try:
        token = await ZendeskOAuthService.exchange_code(
            db=db,
            code=code,
        )

    except ZendeskOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )

    response = {
        "status": "connected",
        "provider": "zendesk",
        "scope": token.scope,
        "expires_at": token.expires_at,
    }

    return response