import os
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.request_context import get_request_id

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development") == "production"


def _sanitize_value(key: str, value: Any) -> Any:
    sensitive_keys = {
        "api_key",
        "apikey",
        "authorization",
        "oauth_token",
        "access_token",
        "refresh_token",
        "client_secret",
        "webhook_secret",
        "password",
        "secret",
    }
    key_lower = key.lower()
    if any(s in key_lower for s in sensitive_keys):
        return "[REDACTED]"

    # Recursively sanitize nested structures
    if isinstance(value, dict):
        return {
            k: _sanitize_value(k, v) for k, v in value.items()
        }
    elif isinstance(value, list):
        return [
            _sanitize_value(f"list_item[{i}]", item) for i, item in enumerate(value)
        ]
    return value


def _add_request_context(logger, method_name, event_dict):
    request_id = get_request_id()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def _add_caller_info(logger, method_name, event_dict):
    event_dict["logger"] = logger._context.get("logger_name", "unknown")
    return event_dict


def configure_logging() -> None:
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        _add_request_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
    ]

    if _is_production():
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(
                colors=sys.stderr.isatty(),
                exception_formatter=structlog.dev.plain_traceback,
            )
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(LOG_LEVEL),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    logger = structlog.get_logger(name)
    return logger.bind(logger_name=name or "app")


def bind_context(
    *,
    job_id: str | None = None,
    ticket_id: int | None = None,
    agent_run_id: str | None = None,
    action: str | None = None,
    tool: str | None = None,
    outcome: str | None = None,
    error_category: str | None = None,
    request_id: str | None = None,
) -> None:
    ctx = {}
    if job_id:
        ctx["job_id"] = job_id
    if ticket_id is not None:
        ctx["ticket_id"] = str(ticket_id)
    if agent_run_id:
        ctx["agent_run_id"] = agent_run_id
    if action:
        ctx["action"] = action
    if tool:
        ctx["tool"] = tool
    if outcome:
        ctx["outcome"] = outcome
    if error_category:
        ctx["error_category"] = error_category
    if request_id:
        ctx["request_id"] = request_id

    bind_contextvars(**{k: _sanitize_value(k, v) for k, v in ctx.items()})


def unbind_context() -> None:
    clear_contextvars()


class LoggingMixin:
    @property
    def log(self):
        if not hasattr(self, "_logger"):
            self._logger = get_logger(self.__class__.__module__)
        return self._logger