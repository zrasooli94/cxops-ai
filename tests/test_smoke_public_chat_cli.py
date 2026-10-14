"""Tests for the public-chat pilot smoke CLI (Phase 1P.3).

This script is the last thing an operator runs before pointing a real customer at
a deployment, and it is the only tool that exercises the whole public path
(widget key -> origin allowlist -> session -> message). Its argument handling
deserves tests because the safe invocation (a key file, so the key never reaches
shell history or the process table) is the one that is easiest to break
silently: a key that is silently dropped still produces a plausible-looking run
against the wrong thing, or an early exit an operator reads as a network fault.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "smoke_public_chat.py"

ORIGIN = "https://www.example.com"
VALID_KEY = "pk_live_" + "a" * 43


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
        # A non-zero exit is the behaviour under test, so never raise.
        check=False,
    )


def test_key_file_alone_is_accepted_and_passes_argument_validation(
    tmp_path: Path,
) -> None:
    """--widget-key-file must not also require --widget-key.

    Before this was fixed the two flags were mutually required, which made the
    documented safe invocation impossible. The run below points at a closed
    local port, so a non-zero exit is expected: what matters is that it fails at
    the network stage, not with an argument error.
    """
    key_file = tmp_path / "widget.key"
    key_file.write_text(VALID_KEY + "\n", encoding="utf-8")

    result = _run(
        "--widget-key-file",
        str(key_file),
        "--embedding-origin",
        ORIGIN,
        "--backend-url",
        "http://127.0.0.1:1",
        "--timeout",
        "2",
    )

    combined = result.stdout + result.stderr
    assert "--widget-key or --widget-key-file" not in combined, combined
    assert "widget key file was empty" not in combined, combined
    # It read the key and reached the first probe. Without the fix this exited 2
    # during argument handling, before printing any stage.
    assert "version endpoint" in combined, combined


def test_missing_both_key_flags_names_both_options() -> None:
    result = _run("--embedding-origin", ORIGIN)

    assert result.returncode == 2, result.stderr
    assert "--widget-key or --widget-key-file" in result.stderr, result.stderr


def test_empty_key_file_is_reported_distinctly(tmp_path: Path) -> None:
    """An empty file is a different operator mistake from a missing flag."""
    key_file = tmp_path / "widget.key"
    key_file.write_text("   \n", encoding="utf-8")

    result = _run(
        "--widget-key-file", str(key_file), "--embedding-origin", ORIGIN
    )

    assert result.returncode == 2, result.stderr
    assert "widget key file was empty" in result.stderr, result.stderr


def test_unreadable_key_file_exits_two(tmp_path: Path) -> None:
    result = _run(
        "--widget-key-file",
        str(tmp_path / "does-not-exist"),
        "--embedding-origin",
        ORIGIN,
    )

    assert result.returncode == 2, result.stderr
    assert "could not read widget key file" in result.stderr, result.stderr


def test_truncated_key_is_rejected_before_any_request() -> None:
    result = _run(
        "--widget-key", "pk_live_short", "--embedding-origin", ORIGIN
    )

    assert result.returncode == 2, result.stderr
    assert "looks truncated" in result.stderr, result.stderr


def test_embedding_origin_is_still_required() -> None:
    """The origin is the allowlist guard; the CLI must not default it away."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--widget-key", VALID_KEY],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
        # A non-zero exit is the behaviour under test, so never raise.
        check=False,
    )

    assert result.returncode == 2
    assert "--embedding-origin" in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# Phase 1P.9R: --delivery async activation check (no network: stub transport)
# ---------------------------------------------------------------------------


def _smoke_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "smoke_public_chat_under_test",
        Path(__file__).resolve().parents[1] / "scripts" / "smoke_public_chat.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _StubSmoke:
    backend_url = "https://api.example.invalid"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def _auth(self):
        return {"Authorization": "Bearer t"}

    def _request(self, url, *, method="GET", payload=None, headers=None):
        self.requests.append((method, url, payload))
        return self.responses.pop(0)


def test_async_delivery_waits_for_the_reply_via_updates() -> None:
    smoke_mod = _smoke_module()
    stub = _StubSmoke(
        [
            (200, {"processing_state": "accepted", "reply": None, "status": "ai_active"}),
            (200, {"messages": [], "cursor": 0, "poll_after_ms": 1000, "status": "ai_active"}),
            (
                200,
                {
                    "messages": [{"id": 9, "direction": "outbound", "body": "Hi there"}],
                    "cursor": 9,
                    "poll_after_ms": 3000,
                    "status": "ai_active",
                },
            ),
        ]
    )
    detail = smoke_mod.send_and_wait_for_reply(
        stub, origin_header={}, client_message_id="smoke-1", delivery="async", sleep=lambda _s: None
    )
    assert "async reply visible" in detail and "Hi there" not in detail
    assert stub.requests[0][2]["delivery"] == "async"
    assert "/sessions/updates?after_id=0" in stub.requests[1][1]


def test_async_delivery_fails_when_the_tenant_is_not_activated() -> None:
    smoke_mod = _smoke_module()
    stub = _StubSmoke([(200, {"processing_state": "completed", "reply": "Hi", "status": "ai_active"})])
    with pytest.raises(smoke_mod.StepFailure, match="not active for this tenant"):
        smoke_mod.send_and_wait_for_reply(
            stub, origin_header={}, client_message_id="smoke-2", delivery="async"
        )


def test_sync_delivery_keeps_the_original_contract() -> None:
    smoke_mod = _smoke_module()
    stub = _StubSmoke([(200, {"processing_state": "completed", "reply": "Hi", "status": "ai_active"})])
    detail = smoke_mod.send_and_wait_for_reply(
        stub, origin_header={}, client_message_id="smoke-3", delivery="sync"
    )
    assert "reply received" in detail
    assert "delivery" not in stub.requests[0][2]


def test_probe_text_is_sent_and_handoff_flag_is_reported_without_the_body() -> None:
    smoke_mod = _smoke_module()
    stub = _StubSmoke(
        [(200, {"processing_state": "completed", "reply": "Secret body", "status": "ai_active", "handoff": False})]
    )
    detail = smoke_mod.send_and_wait_for_reply(
        stub,
        origin_header={},
        client_message_id="smoke-4",
        delivery="sync",
        probe_text="Does RISPU develop mobile applications?",
    )
    assert stub.requests[0][2]["text"] == "Does RISPU develop mobile applications?"
    assert "handoff=False" in detail and "Secret body" not in detail


# ---------------------------------------------------------------------------
# Phase 1R: release-identity stop and explicit grounded-reply expectation
# ---------------------------------------------------------------------------

import argparse

SHA = "c9e55b757509f661edf6dcecaaa3bf186b6bf495"


class _ScriptedSmoke:
    """Replaces ``Smoke`` inside ``run``; records every request."""

    version_payload: ClassVar[dict] = {"service": "CXOps AI", "version": "0.1.0", "environment": "production"}
    message_payload: ClassVar[dict] = {}
    raise_on_message: Exception | None = None
    instances: ClassVar[list] = []

    def __init__(self, backend_url, frontend_url, timeout):
        self.backend_url = backend_url
        self.frontend_url = frontend_url
        self.session_token = None
        self.requests: list[tuple[str, str]] = []
        type(self).instances.append(self)

    def _auth(self):
        return {"Authorization": "Bearer t"}

    def _request(self, url, *, method="GET", payload=None, headers=None):
        self.requests.append((method, url))
        if url.endswith("/version"):
            return 200, dict(self.version_payload)
        if url.endswith("/health"):
            return 200, {"status": "healthy"}
        if url.endswith("/ready"):
            return 200, {"status": "ready", "checks": {"database": "ok"}}
        if "/chat/embed" in url:
            return 200, {}
        if url.endswith("/public/chat/sessions") and method == "POST":
            return 200, {"session": {"token": "tok", "status": "ai_active"}, "config": {}}
        if url.endswith("/public/chat/messages") and method == "POST":
            if payload and str(payload.get("client_message_id", "")).startswith("smoke-closed"):
                return 409, {}
            if self.raise_on_message is not None:
                raise self.raise_on_message
            return 200, dict(self.message_payload)
        if url.endswith("/sessions/human"):
            return 200, {"status": "human_requested"}
        if url.endswith("/sessions/close"):
            return 200, {}
        raise AssertionError(f"unexpected request {method} {url}")

    def posted(self, suffix: str) -> int:
        return sum(1 for m, u in self.requests if m == "POST" and u.endswith(suffix))


def _args(**overrides) -> argparse.Namespace:
    base = {
        "backend_url": "https://api.example.invalid",
        "frontend_url": "https://web.example.invalid",
        "widget_key": "pk_live_" + "a" * 40,
        "embedding_origin": "https://www.rispu.com",
        "timeout": 5.0,
        "delivery": "sync",
        "probe_text": "Does RISPU develop mobile applications?",
        "expect_build_sha": "",
        "expect_grounded_reply": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _run_smoke(monkeypatch, **overrides):
    smoke_mod = _smoke_module()
    _ScriptedSmoke.instances = []
    monkeypatch.setattr(smoke_mod, "Smoke", _ScriptedSmoke)
    code = smoke_mod.run(_args(**overrides))
    return code, _ScriptedSmoke.instances[-1]


GOOD = {
    "reply": "RISPU provides Android and iOS development.",
    "handoff": False,
    "processing_state": "completed",
    "status": "ai_active",
}


@pytest.mark.parametrize(
    "version_payload",
    [
        {"build_sha": "d" * 40},  # different commit
        {"build_sha": None},  # reported as unknown
        {},  # older build with no field at all
        {"build_sha": "main"},  # not a commit id
        {"build_sha": SHA.upper()},  # not the canonical lowercase form
        {"build_sha": 12345},
    ],
)
def test_build_identity_mismatch_stops_before_any_session_or_model_call(
    monkeypatch, capsys, version_payload
) -> None:
    _ScriptedSmoke.version_payload = {"service": "CXOps AI", "version": "0.1.0", **version_payload}
    _ScriptedSmoke.message_payload = GOOD
    code, smoke = _run_smoke(monkeypatch, expect_build_sha=SHA)

    assert code == 1
    assert smoke.posted("/public/chat/sessions") == 0
    assert smoke.posted("/public/chat/messages") == 0
    assert smoke.posted("/sessions/human") == 0
    assert [u for _m, u in smoke.requests] == ["https://api.example.invalid/version"]
    assert "SMOKE: FAIL" in capsys.readouterr().out


def test_matching_build_identity_proceeds(monkeypatch) -> None:
    _ScriptedSmoke.version_payload = {"service": "CXOps AI", "version": "0.1.0", "build_sha": SHA}
    _ScriptedSmoke.message_payload = GOOD
    code, smoke = _run_smoke(monkeypatch, expect_build_sha=SHA, expect_grounded_reply=True)
    assert code == 0
    assert smoke.posted("/public/chat/sessions") == 1


@pytest.mark.parametrize(
    "payload",
    [
        {**GOOD, "handoff": True, "status": "human_requested", "processing_state": "handoff"},
        {k: v for k, v in GOOD.items() if k != "handoff"},  # handoff absent
        {**GOOD, "handoff": None},
        {**GOOD, "processing_state": "accepted"},
        {**GOOD, "status": "human_requested"},
        {**GOOD, "reply": ""},
        {**GOOD, "reply": "   "},
        {**GOOD, "reply": "A support team member needs to review this request."},
        {**GOOD, "reply": "Your request has been received."},
        {**GOOD, "reply": "I don't have enough information in the knowledge base to answer that question."},
    ],
)
def test_expect_grounded_reply_fails_and_still_closes_the_session(monkeypatch, capsys, payload) -> None:
    _ScriptedSmoke.version_payload = {"service": "CXOps AI", "version": "0.1.0", "build_sha": SHA}
    _ScriptedSmoke.message_payload = payload
    code, smoke = _run_smoke(monkeypatch, expect_grounded_reply=True)

    out = capsys.readouterr().out
    assert code == 1
    assert "SMOKE: FAIL" in out
    assert "FAIL  message answered" in out
    assert smoke.posted("/sessions/close") == 1  # the created session is closed
    assert GOOD["reply"] not in out  # bodies are never printed


def test_expect_grounded_reply_passes_only_with_every_signal(monkeypatch, capsys) -> None:
    _ScriptedSmoke.message_payload = GOOD
    code, smoke = _run_smoke(monkeypatch, expect_grounded_reply=True)
    out = capsys.readouterr().out
    assert code == 0
    assert "handoff=False" in out
    assert "HTTP metadata only" in out  # does not claim RAG grounding is proven
    assert GOOD["reply"] not in out
    assert smoke.posted("/sessions/close") == 1


def test_grounded_expectation_omitted_keeps_original_behaviour(monkeypatch) -> None:
    # A handoff reply was always acceptable to the original smoke test.
    _ScriptedSmoke.message_payload = {
        "reply": "A support team member needs to review this request.",
        "handoff": True,
        "processing_state": "handoff",
        "status": "human_requested",
    }
    code, _smoke = _run_smoke(monkeypatch)
    assert code == 0


def test_session_is_closed_even_when_the_message_step_blows_up(monkeypatch, capsys) -> None:
    _ScriptedSmoke.message_payload = GOOD
    monkeypatch.setattr(_ScriptedSmoke, "raise_on_message", RuntimeError("boom with a secret body"))
    try:
        code, smoke = _run_smoke(monkeypatch, expect_grounded_reply=True)
    finally:
        _ScriptedSmoke.raise_on_message = None
    out = capsys.readouterr().out
    assert code == 1
    assert smoke.posted("/sessions/close") == 1
    assert "secret body" not in out and "RuntimeError" in out


def test_static_reply_set_matches_the_application_constants() -> None:
    from app.services.public_chat_service import (
        FALLBACK_REPLY,
        HANDOFF_REPLY,
        LOCAL_DEMO_HANDOFF_REPLY,
    )
    from app.services.rag_reliability import FALLBACK_STRINGS

    static = _smoke_module().STATIC_REPLIES
    for text in (HANDOFF_REPLY, FALLBACK_REPLY, LOCAL_DEMO_HANDOFF_REPLY, *FALLBACK_STRINGS):
        assert text in static


@pytest.mark.parametrize(
    "extra",
    [
        ["--expect-grounded-reply", "--delivery", "async"],
        ["--expect-build-sha", "main"],
    ],
)
def test_invalid_expectation_flags_exit_two_before_any_request(extra) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--widget-key",
            "pk_live_" + "a" * 40,
            "--embedding-origin",
            "https://www.rispu.com",
            "--backend-url",
            "http://127.0.0.1:1",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
