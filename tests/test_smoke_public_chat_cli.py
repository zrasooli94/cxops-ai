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
