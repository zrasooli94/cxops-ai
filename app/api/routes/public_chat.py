"""Public web-chat API (Phase 1P.1).

These routes are intentionally free of control-center authentication: the
tenant is resolved server-side from the public widget key, and sessions are
authenticated by their end-to-end token (bearer). No ``RequireCapability`` or
staff ``AuthorizationContext`` is used here.
"""

from typing import Annotated, ClassVar

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.public_chat import (
    PublicChatCloseResponse,
    PublicChatConfigResponse,
    PublicChatHumanRequestResponse,
    PublicChatMessageSendRequest,
    PublicChatMessageSendResponse,
    PublicChatSessionCreateRequest,
    PublicChatSessionCreateResponse,
    PublicChatSessionSummary,
    PublicChatStateResponse,
)
from app.services.public_chat_service import (
    PublicChatConfigurationNotFoundError,
    PublicChatInputTooLongError,
    PublicChatMessageInFlightError,
    PublicChatOriginNotAllowedError,
    PublicChatRateLimitedError,
    PublicChatSessionClosedError,
    PublicChatSessionExpiredError,
    PublicChatSessionNotFoundError,
    PublicChatWidgetDisabledError,
    public_chat_service,
)

router = APIRouter(
    prefix="/public/chat",
    tags=["Public Web Chat"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

EMBEDDING_ORIGIN_HEADER = "x-embedding-origin"


def embedding_origin(request: Request) -> str | None:
    return request.headers.get(EMBEDDING_ORIGIN_HEADER)


def require_session_token(request: Request) -> str:
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid session token",
        )
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid session token",
        )
    return token


Payload = Annotated[str, Depends(require_session_token)]
Origin = Annotated[str | None, Depends(embedding_origin)]


class PublicChatRouteErrors:
    """Central mapping of service errors to public HTTP responses.

    Kept as a mapping so every endpoint shares one behavior: configuration
    problems are 404/403, session problems are 401/409, abuse guards are 429,
    malformed input is 400.
    """

    _STATUSES: ClassVar[dict[type[Exception], int]] = {
        PublicChatConfigurationNotFoundError: status.HTTP_404_NOT_FOUND,
        PublicChatWidgetDisabledError: status.HTTP_403_FORBIDDEN,
        PublicChatOriginNotAllowedError: status.HTTP_403_FORBIDDEN,
        PublicChatSessionNotFoundError: status.HTTP_401_UNAUTHORIZED,
        PublicChatSessionExpiredError: status.HTTP_401_UNAUTHORIZED,
        PublicChatSessionClosedError: status.HTTP_409_CONFLICT,
        PublicChatRateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
        PublicChatInputTooLongError: status.HTTP_400_BAD_REQUEST,
        PublicChatMessageInFlightError: status.HTTP_409_CONFLICT,
    }

    @classmethod
    def raise_checked(cls, exc: Exception) -> None:
        http_code = cls._STATUSES.get(type(exc))
        if http_code is None:
            raise exc
        raise HTTPException(
            status_code=http_code,
            detail=str(exc),
        ) from exc


def _config_response(config) -> PublicChatConfigResponse:
    return PublicChatConfigResponse(
        display_name=config.display_name,
        welcome_message=config.welcome_message,
        enabled=config.enabled,
        theme_token=config.theme_token,
        max_message_length=config.max_message_length,
    )


@router.get(
    "/config",
    response_model=PublicChatConfigResponse,
)
async def get_public_chat_config(
    db: DatabaseSession,
    key: str = Query(min_length=8, max_length=128),
):
    """Bounded public configuration for a widget key (no session required).

    The config is public by design; it grants neither staff access nor tool
    authorization. The embedding-origin allowlist is never serialized here.
    """
    try:
        configuration = await public_chat_service.resolve_config(db, key)
    except (
        PublicChatConfigurationNotFoundError,
        PublicChatWidgetDisabledError,
    ) as exc:
        PublicChatRouteErrors.raise_checked(exc)

    return _config_response(configuration)


@router.post(
    "/sessions",
    response_model=PublicChatSessionCreateResponse,
)
async def create_public_chat_session(
    body: PublicChatSessionCreateRequest,
    db: DatabaseSession,
    origin: Origin,
):
    try:
        result = await public_chat_service.create_session(
            db,
            public_widget_key=body.public_widget_key,
            embedding_origin=origin,
        )
    except (
        PublicChatConfigurationNotFoundError,
        PublicChatWidgetDisabledError,
        PublicChatOriginNotAllowedError,
        PublicChatRateLimitedError,
    ) as exc:
        PublicChatRouteErrors.raise_checked(exc)

    configuration = result["configuration"]
    return PublicChatSessionCreateResponse(
        config=_config_response(configuration),
        session=PublicChatSessionSummary(
            token=result["token"],
            status=result["session"].status,
            expires_at=result["session"].expires_at,
        ),
    )


@router.post(
    "/messages",
    response_model=PublicChatMessageSendResponse,
)
async def send_public_chat_message(
    body: PublicChatMessageSendRequest,
    db: DatabaseSession,
    session_token: Payload,
    origin: Origin,
):
    try:
        result = await public_chat_service.send_message(
            db,
            session_token=session_token,
            embedding_origin=origin,
            text=body.text,
            client_message_id=body.client_message_id,
        )
    except (
        PublicChatConfigurationNotFoundError,
        PublicChatOriginNotAllowedError,
        PublicChatSessionNotFoundError,
        PublicChatSessionExpiredError,
        PublicChatSessionClosedError,
        PublicChatRateLimitedError,
        PublicChatInputTooLongError,
        PublicChatMessageInFlightError,
    ) as exc:
        PublicChatRouteErrors.raise_checked(exc)

    return PublicChatMessageSendResponse(**result)


@router.get(
    "/sessions/state",
    response_model=PublicChatStateResponse,
)
async def get_public_chat_state(
    db: DatabaseSession,
    session_token: Payload,
):
    try:
        session = await public_chat_service.verify_session(db, session_token)
        state = await public_chat_service.get_state(db, session)
    except (
        PublicChatSessionNotFoundError,
        PublicChatSessionExpiredError,
        PublicChatSessionClosedError,
    ) as exc:
        PublicChatRouteErrors.raise_checked(exc)

    return PublicChatStateResponse(**state)


@router.post(
    "/sessions/human",
    response_model=PublicChatHumanRequestResponse,
)
async def request_human_support(
    db: DatabaseSession,
    session_token: Payload,
):
    try:
        session = await public_chat_service.verify_session(db, session_token)
        result = await public_chat_service.request_human(db, session)
    except (
        PublicChatSessionNotFoundError,
        PublicChatSessionExpiredError,
        PublicChatSessionClosedError,
    ) as exc:
        PublicChatRouteErrors.raise_checked(exc)

    return PublicChatHumanRequestResponse(**result)


@router.post(
    "/sessions/close",
    response_model=PublicChatCloseResponse,
)
async def close_public_chat_session(
    db: DatabaseSession,
    session_token: Payload,
):
    try:
        session = await public_chat_service.verify_session(db, session_token)
        result = await public_chat_service.close_session(db, session)
    except (
        PublicChatSessionNotFoundError,
        PublicChatSessionExpiredError,
        PublicChatSessionClosedError,
    ) as exc:
        PublicChatRouteErrors.raise_checked(exc)

    return PublicChatCloseResponse(**result)