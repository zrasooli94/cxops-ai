import re
import uuid
from contextvars import ContextVar

import structlog
from starlette.datastructures import Headers

REQUEST_ID_HEADER = "x-request-id"

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


request_id_var: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)


def sanitize_request_id(
    value: str | None,
) -> str | None:

    if not value:
        return None

    candidate = value.strip()

    if len(candidate) > 128:
        return None

    if not _VALID_REQUEST_ID.match(candidate):
        return None

    return candidate


def get_request_id() -> str | None:

    return request_id_var.get()


class RequestCorrelationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(
        self,
        scope,
        receive,
        send,
    ) -> None:

        if scope["type"] != "http":
            await self.app(
                scope,
                receive,
                send,
            )
            return

        headers = Headers(scope=scope)

        request_id = (
            sanitize_request_id(headers.get(REQUEST_ID_HEADER)) or uuid.uuid4().hex
        )

        token = request_id_var.set(request_id)

        structlog.contextvars.bind_contextvars(request_id=request_id)

        header_name = REQUEST_ID_HEADER.encode("latin-1")

        header_value = request_id.encode("latin-1")

        async def send_with_request_id(message):
            if message[
                "type"
            ] == "http.response.start" and headers_list_has_no_request_id(message):
                message["headers"] = [
                    *message["headers"],
                    (header_name, header_value),
                ]

            await send(message)

        try:
            await self.app(
                scope,
                receive,
                send_with_request_id,
            )

        finally:
            request_id_var.reset(token)

            structlog.contextvars.clear_contextvars()


def headers_list_has_no_request_id(message) -> bool:

    return not any(
        name == b"x-request-id" for name, _value in (message.get("headers") or [])
    )
