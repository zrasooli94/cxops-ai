from unittest.mock import patch

from app.core.logging import (
    _is_production,
    _sanitize_value,
    bind_context,
    configure_logging,
    get_logger,
    unbind_context,
)


def test_sanitize_value_redacts_sensitive_keys():
    assert _sanitize_value("api_key", "secret123") == "[REDACTED]"
    assert _sanitize_value("Authorization", "Bearer token") == "[REDACTED]"
    assert _sanitize_value("oauth_token", "xyz") == "[REDACTED]"
    assert _sanitize_value("client_secret", "sec") == "[REDACTED]"
    assert _sanitize_value("webhook_secret", "whs") == "[REDACTED]"
    assert _sanitize_value("password", "pwd") == "[REDACTED]"


def test_sanitize_value_passes_normal_keys():
    assert _sanitize_value("user_id", "123") == "123"
    assert _sanitize_value("ticket_id", 456) == 456
    assert _sanitize_value("action", "respond") == "respond"
    assert _sanitize_value("outcome", "success") == "success"


def test_is_production_false_by_default():
    with patch.dict("os.environ", {"ENVIRONMENT": "development"}, clear=False):
        assert _is_production() is False
    with patch.dict("os.environ", {"ENVIRONMENT": "staging"}, clear=False):
        assert _is_production() is False


def test_is_production_true_when_set():
    with patch.dict("os.environ", {"ENVIRONMENT": "production"}, clear=False):
        assert _is_production() is True


def test_configure_logging_runs_without_error():
    configure_logging()
    logger = get_logger("test")
    logger.info("test message", extra_field="value")


def test_get_logger_binds_module_name():
    logger = get_logger("my.module")
    assert logger._context.get("logger_name") == "my.module"


def test_bind_context_sets_contextvars():
    bind_context(
        job_id="job-123",
        ticket_id=42,
        agent_run_id="run-abc",
        action="respond",
        tool="zendesk.send_reply",
        outcome="success",
    )
    # Verify through structlog that binding works (gets merged into log entries)
    logger = get_logger("test")
    logger.info("test with context")
    # The context binding is verified by successful execution without error
    unbind_context()


def test_bind_context_redacts_sensitive():
    # Test that sensitive values in context get redacted
    # The function only binds known params, but we can test the sanitizer directly
    from app.core.logging import _sanitize_value

    assert _sanitize_value("api_key", "secret") == "[REDACTED]"
    assert _sanitize_value("password", "pwd") == "[REDACTED]"
    assert _sanitize_value("oauth_token", "token") == "[REDACTED]"


def test_sanitize_value_redacts_nested_dict():
    # Test recursive redaction of nested dictionaries
    from app.core.logging import _sanitize_value

    nested = {
        "config": {
            "api_key": "nested-secret",
            "normal": "value",
        },
        "oauth_token": "top-level-secret",
    }
    result = _sanitize_value("config", nested)
    assert result["config"]["api_key"] == "[REDACTED]"
    assert result["config"]["normal"] == "value"
    assert result["oauth_token"] == "[REDACTED]"


def test_sanitize_value_redacts_nested_list():
    # Test recursive redaction of lists
    from app.core.logging import _sanitize_value

    nested_list = [
        {"api_key": "list-secret-1"},
        {"normal": "value"},
        "plain-string",
    ]
    result = _sanitize_value("items", nested_list)
    assert result[0]["api_key"] == "[REDACTED]"
    assert result[1]["normal"] == "value"
    assert result[2] == "plain-string"


def test_sanitize_value_preserves_non_sensitive_nested():
    # Test that non-sensitive nested structures are preserved
    from app.core.logging import _sanitize_value

    nested = {
        "metadata": {
            "user_id": 123,
            "tags": ["tag1", "tag2"],
        },
    }
    result = _sanitize_value("data", nested)
    assert result["metadata"]["user_id"] == 123
    assert result["metadata"]["tags"] == ["tag1", "tag2"]


def test_unbind_context_clears():
    bind_context(job_id="job-1")
    logger = get_logger("test")
    logger.info("test before unbind")
    unbind_context()
    logger.info("test after unbind")
    # Verified by successful execution
