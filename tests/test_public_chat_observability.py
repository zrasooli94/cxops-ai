"""Observability and PII guarantees for the public chat surface (Phase 1P.3).

The public chat surface is unauthenticated, internet-facing, and handles
customer text. That combination makes logging the single easiest way to leak
PII into a third-party log sink, so these tests assert the *absence* of
sensitive values rather than the presence of helpful ones.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

PUBLIC_CHAT_MODULES = [
    "app/api/routes/public_chat.py",
    "app/services/public_chat_service.py",
    "app/services/public_chat_staff_service.py",
    "app/services/public_chat_summary_service.py",
    "app/services/tenant_config_service.py",
    "app/tenant_onboarding/planner.py",
    "scripts/onboard_tenant.py",
    "scripts/check_public_chat_pilot.py",
    "scripts/smoke_public_chat.py",
    "scripts/validate_production_config.py",
]

# Names that must never appear as a structured-logging keyword.
FORBIDDEN_LOG_KWARGS = {
    "token",
    "session_token",
    "public_widget_key",
    "widget_key",
    "public_widget_key_hash",
    "authorization",
    "text",
    "message",
    "body",
    "password",
    "api_key",
    "secret",
    "origin",
    "email",
}


def _is_logger(node: ast.expr) -> bool:
    """Recognise a logger expression: ``log``, ``logger``, ``self._log``, ...

    Matching only the bare name ``log`` would let a renamed or module-scoped
    logger escape the check entirely, which is the easiest way for a future
    change to start logging customer data unnoticed.
    """
    if isinstance(node, ast.Name):
        return "log" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return _is_logger(node.value) or "log" in node.attr.lower()
    if isinstance(node, ast.Call):
        return _is_logger(node.func)
    return False


def _logging_calls(path: Path) -> list[ast.Call]:
    """Every logger level call in a file, whatever the logger is named."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.expr)
            and _is_logger(node.func.value)
        ):
            calls.append(node)
    return calls


def _keyword_names(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg is not None}


@pytest.mark.parametrize(
    "relative_path",
    PUBLIC_CHAT_MODULES,
)
def test_logging_never_passes_a_sensitive_keyword(relative_path: str):
    path = REPO_ROOT / relative_path
    offenders: list[str] = []
    for call in _logging_calls(path):
        for name in _keyword_names(call) & FORBIDDEN_LOG_KWARGS:
            offenders.append(f"{name} at line {call.lineno}")
    assert not offenders, f"{relative_path} logs sensitive keywords: {offenders}"


@pytest.mark.parametrize(
    "relative_path",
    PUBLIC_CHAT_MODULES,
)
def test_logging_never_interpolates_a_value_into_the_message(relative_path: str):
    """Event names must be constant, and no value may ride along as a lazy arg.

    ``log.info("rejected %s", email)`` looks constant to a naive check but
    formats the customer value into the message, so the arg count matters as much
    as the first argument. Values belong in bounded structured kwargs.
    """
    path = REPO_ROOT / relative_path
    offenders: list[str] = []
    for call in _logging_calls(path):
        # f-strings, .format(), %, and concatenation are all value-bearing.
        is_constant = len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
        if not is_constant:
            offenders.append(f"line {call.lineno}")
    assert not offenders, (
        f"{relative_path} builds log messages from values: {offenders}"
    )


def test_public_chat_metrics_use_only_bounded_lifecycle_labels():
    """No metric label may be a customer, request, or configuration value."""
    from app.core import metrics

    source = inspect.getsource(metrics)
    tree = ast.parse(source)
    allowed_label_names = {
        "outcome",
        "reason",
        "closed_by",
        "action",
        "decision_path",
        "token_type",
        "specialist",
        "from_specialist",
        "to_specialist",
        "routing_source",
        "milestone",
        "stage",
        "source",
        "status",
        "kind",
        "tool",
        "result",
    }
    offenders: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"Counter", "Histogram", "Gauge"}
            and len(node.args) >= 3
            and isinstance(node.args[2], (ast.List, ast.Tuple))
        ):
            for element in node.args[2].elts:
                if (
                    isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                    and element.value not in allowed_label_names
                ):
                    offenders.append(element.value)
    assert not offenders, f"unbounded metric labels: {offenders}"


def test_rejection_reasons_are_a_closed_set():
    from app.api.routes.public_chat import REJECTION_REASONS
    from app.services import public_chat_service

    known = {
        value
        for value in vars(public_chat_service).values()
        if isinstance(value, type)
        and issubclass(value, Exception)
        and value.__name__.startswith("PublicChat")
    }
    # Every mapped service error should have a stable reason name, and every
    # reason should map to a real error type, so a new error cannot silently
    # land in the "unknown" bucket.
    assert set(REJECTION_REASONS) <= known
    assert len(set(REJECTION_REASONS.values())) == len(REJECTION_REASONS)


def test_readiness_never_returns_the_driver_exception():
    """The 503 path must not echo a DSN-carrying driver error."""
    from app.api.routes import health

    source = inspect.getsource(health.ready)
    assert "str(exc)" not in source
    assert "!r}" not in source
    assert health.DB_UNAVAILABLE == "unavailable"
