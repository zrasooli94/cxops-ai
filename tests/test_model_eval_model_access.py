from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from app.services.model_eval_model_access import (
    MODEL_ACCESS_ENDPOINT,
    ModelAccessResult,
    check_gpt6_luna_model_access,
)
from scripts import verify_controlled_eval_model_access as launcher


class _Response:
    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = json.dumps(body).encode()

    def read(self, amount: int = -1) -> bytes:
        return self.body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _Opener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


def test_exact_single_get_and_sanitized_success(capsys):
    sentinel = "synthetic-secret-sentinel"
    opener = _Opener(_Response(200, {"id": "gpt-6-luna", "object": "model"}))
    result = check_gpt6_luna_model_access(sentinel, opener=opener)
    assert result == ModelAccessResult(200, True, None)
    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert request.full_url == MODEL_ACCESS_ENDPOINT
    assert request.method == "GET"
    assert request.data is None
    assert timeout == 15.0
    assert sentinel not in json.dumps(result.sanitized())
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err


@pytest.mark.parametrize(
    ("status", "category"),
    [(302, "redirect_blocked"), (401, "authentication_rejected"), (403, "permission_denied"), (404, "model_not_found_or_inaccessible"), (429, "rate_limited"), (500, "provider_server_error")],
)
def test_http_failures_are_one_shot_and_sanitized(status, category):
    opener = _Opener(
        error=HTTPError(MODEL_ACCESS_ENDPOINT, status, "secret-bearing detail", {}, None)
    )
    result = check_gpt6_luna_model_access("synthetic-token", opener=opener)
    assert result == ModelAccessResult(status, False, category)
    assert len(opener.calls) == 1
    assert "secret-bearing detail" not in json.dumps(result.sanitized())


def test_invalid_metadata_does_not_trigger_fallback_request():
    opener = _Opener(_Response(200, {"id": "another-model", "object": "model"}))
    result = check_gpt6_luna_model_access("synthetic-token", opener=opener)
    assert result == ModelAccessResult(200, False, "invalid_metadata")
    assert len(opener.calls) == 1


def test_launcher_checks_flags_before_prompt(monkeypatch, capsys):
    prompted = False

    def prompt(_operation):
        nonlocal prompted
        prompted = True
        raise AssertionError("must not prompt")

    monkeypatch.setattr(launcher, "run_with_operator_api_key", prompt)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MODEL_EVAL_OPENAI_SOURCE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_CHAT_MODEL_EVAL_ENABLED", "false")
    assert launcher.main() == 2
    assert prompted is False
    assert json.loads(capsys.readouterr().out) == {
        "error_category": "local_preflight_blocked",
        "http_status": None,
        "metadata_accessible": False,
    }


def test_launcher_rejects_inherited_credential_before_prompt(monkeypatch, capsys):
    prompted = False

    def prompt(_operation):
        nonlocal prompted
        prompted = True
        raise AssertionError("must not prompt")

    monkeypatch.setattr(launcher, "run_with_operator_api_key", prompt)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret-sentinel")
    assert launcher.main() == 2
    assert prompted is False
    assert json.loads(capsys.readouterr().out)["error_category"] == (
        "local_preflight_blocked"
    )


def test_launcher_output_is_bounded_and_contains_no_secret(monkeypatch, capsys):
    sentinel = "synthetic-secret-sentinel"
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MODEL_EVAL_OPENAI_SOURCE_ENABLED", "false")
    monkeypatch.setenv("PUBLIC_CHAT_MODEL_EVAL_ENABLED", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EVALUATION_OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        launcher,
        "run_with_operator_api_key",
        lambda operation: operation(sentinel),
    )
    monkeypatch.setattr(
        launcher,
        "check_gpt6_luna_model_access",
        lambda _secret: ModelAccessResult(200, True, None),
    )
    assert launcher.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "error_category": None,
        "http_status": 200,
        "metadata_accessible": True,
    }
    assert sentinel not in output
