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
from app.core.logging import get_logger
from app.core.metrics import (
    record_public_chat_handoff,
    record_public_chat_message,
    record_public_chat_rejection,
    record_public_chat_session,
)
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

log = get_logger(__name__)

# Stable, low-cardinality reason names for metrics and logs. Exception text is
# never used as a metric label: it can embed a DSN or a customer string, and it
# would create unbounded label cardinality.
REJECTION_REASONS: dict[type[Exception], str] = {
    PublicChatConfigurationNotFoundError: "config_not_found",
    PublicChatWidgetDisabledError: "widget_disabled",
    PublicChatOriginNotAllowedError: "origin_not_allowed",
    PublicChatSessionNotFoundError: "session_not_found",
    PublicChatSessionExpiredError: "session_expired",
    PublicChatSessionClosedError: "session_closed",
    PublicChatRateLimitedError: "rate_limited",
    PublicChatInputTooLongError: "input_too_long",
    PublicChatMessageInFlightError: "message_in_flight",
}


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
        # A refused request is a real operational signal: during a pilot it is
        # the first visible symptom of a wrong origin, an exhausted rate limit,
        # or a rotated key. The type name is the only thing logged, so a
        # customer string in an exception message cannot reach the log sink.
        record_public_chat_rejection(
            reason=REJECTION_REASONS.get(type(exc), "unknown")
        )
        log.info(
            "public_chat_request_rejected",
            reason=REJECTION_REASONS.get(type(exc), "unknown"),
            status_code=http_code,
        )
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

    record_public_chat_session(outcome="created")
    # request_id is added to every record by the logging formatter, so these
    # low-cardinality identifiers are what turn a pilot failure into a
    # findable conversation. Never the session token, key, or message text.
    log.info(
        "public_chat_session_created",
        organization_id=result["configuration"].organization_id,
        session_id=result["session"].id,
    )
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

    record_public_chat_message(outcome=result.get("status", "processed"))
    # No message text, no session token, no widget key: identifiers and the
    # outcome are the whole record.
    # The response contract carries no session identifiers, so only the message
    # id and the outcome are logged here; the service logs the session it used.
    log.info(
        "public_chat_message_processed",
        message_id=result.get("message_id"),
        status=result.get("status", "processed"),
    )
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

    record_public_chat_handoff(outcome="requested")
    log.info(
        "public_chat_handoff_requested",
        organization_id=session.organization_id,
        session_id=session.id,
        session_status=session.status,
    )
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
        session = await public_chat_service.verify_session_for_close(
            db,
            session_token,
        )
        result = await public_chat_service.close_session(db, session)
    except (
        PublicChatSessionNotFoundError,
        PublicChatSessionExpiredError,
        PublicChatSessionClosedError,
    ) as exc:
        PublicChatRouteErrors.raise_checked(exc)

    # The close metric and log are emitted by the shared resolution lifecycle
    # only when this request actually transitions the session to closed, so a
    # retried "End conversation" on an already-closed session converges without
    # double-counting the audit signal.
    return PublicChatCloseResponse(**result)