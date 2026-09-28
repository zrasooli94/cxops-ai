"""Pin the DEBUG -> SQL parameter echo coupling and the gate that blocks it.

`app.core.database` builds the engine with `echo=settings.debug`. SQLAlchemy
echo does not log only statement text: at INFO it also logs bound parameters.
Every public-chat message, model draft, and retrieved knowledge chunk is passed
as a bound parameter, so a production deploy with DEBUG=true writes full customer
content to the application log.

Two things therefore have to hold, and neither is obvious from either file alone:
the engine echo must stay tied to `debug`, and the production gate must reject
`debug`. If the coupling is ever removed, the PII risk returns even with the
gate in place; if the gate is ever weakened, the coupling turns DEBUG into a
data-exfiltration switch.
"""

from __future__ import annotations

import ast
import pathlib
import types

import pytest

DATABASE_MODULE = pathlib.Path("app/core/database.py")
VALIDATOR = pathlib.Path("scripts/validate_production_config.py")


def _database_source() -> str:
    return DATABASE_MODULE.read_text(encoding="utf-8")


def test_engine_echo_is_tied_to_the_debug_flag() -> None:
    """Echo must remain a function of `debug`, not a constant."""
    tree = ast.parse(_database_source())
    echoes = [
        keyword
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "create_async_engine"
        for keyword in node.keywords
        if keyword.arg == "echo"
    ]

    assert echoes, "create_async_engine no longer passes echo= at all"
    for echo in echoes:
        assert isinstance(echo.value, ast.Attribute), (
            "echo= must read from settings (an attribute access), not a literal; "
            "a hardcoded True would log every bound parameter unconditionally"
        )
        assert echo.value.attr == "debug", (
            f"echo is bound to settings.{echo.value.attr}, not settings.debug; "
            "the production DEBUG gate no longer controls SQL parameter logging"
        )


def test_echo_is_never_a_bare_true_literal() -> None:
    """A literal `echo=True` defeats every DEBUG-based mitigation."""
    source = _database_source()
    assert "echo=True" not in source
    assert "echo = True" not in source


def test_production_gate_fails_on_debug() -> None:
    """The validator must fail, not merely warn, when DEBUG is on."""
    source = VALIDATOR.read_text(encoding="utf-8")
    tree = ast.parse(source)

    fails_on_debug = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.get_source_segment(source, node) or ""
        if "cfg.debug" not in body:
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and getattr(inner.func, "attr", None) == "fail"
            ):
                fails_on_debug = True

    assert fails_on_debug, (
        "no reporter.fail(...) on cfg.debug; a production deploy could enable "
        "DEBUG and start writing customer content to logs"
    )


def test_debug_gate_actually_fails() -> None:
    """Exercise the gate: a debug config must be reported as a failure."""
    import scripts.validate_production_config as validator  # type: ignore

    class _Cfg:
        environment = "production"
        debug = True

    reporter = validator.Reporter(fail_on_warn=True)
    validator.check_runtime_flags(_Cfg(), reporter)

    assert reporter.failures > 0, (
        "check_runtime_flags accepted debug=True without recording a failure"
    )
    assert any("DEBUG" in line for line in reporter.lines), reporter.lines


def test_non_debug_config_passes_the_flag_check() -> None:
    """Guard the guard: the flag check must not fail unconditionally."""
    import scripts.validate_production_config as validator  # type: ignore

    class _Cfg:
        environment = "production"
        debug = False

    reporter = validator.Reporter(fail_on_warn=True)
    validator.check_runtime_flags(_Cfg(), reporter)

    assert reporter.failures == 0, reporter.lines
    assert any("DEBUG" in line for line in reporter.lines), reporter.lines


@pytest.mark.parametrize(
    "value,expected_echo",
    [(True, True), (False, False)],
)
def test_echo_follows_debug_for_both_settings(
    value: bool, expected_echo: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build the engine both ways and confirm echo tracks the flag.

    Executing the real module (rather than reading the source) keeps this honest
    if the engine construction is ever refactored into a helper.
    """
    import app.core.config as config_module

    fake_settings = types.SimpleNamespace(
        database_url="postgresql+asyncpg://u:p@localhost:5432/app",
        debug=value,
    )
    monkeypatch.setattr(config_module, "settings", fake_settings)

    captured: dict[str, object] = {}

    def _capture(url, **kwargs):
        captured["echo"] = kwargs.get("echo")
        raise RuntimeError("stop-after-construction")

    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.create_async_engine", _capture
    )
    for name in ("engine", "AsyncSessionLocal"):
        monkeypatch.delattr("app.core.database", name, raising=False)

    import importlib

    with pytest.raises(RuntimeError, match="stop-after-construction"):
        importlib.reload(importlib.import_module("app.core.database"))

    assert captured["echo"] is expected_echo


# ---------------------------------------------------------------------------
# Where the echoed parameters end up
#
# Blocking DEBUG in production is only half the control. The other half is that
# the log file a developer generates while validating locally must not become a
# committable artifact. That happened during this phase: running the API against
# a real database produced a 139 KB log containing customer message content
# (vehicle descriptions, email addresses) which sat untracked but *not*
# ignored, one `git add .` away from being committed.
# ---------------------------------------------------------------------------


def test_generated_logs_are_gitignored() -> None:
    """`*.log` must be ignored, or echoed SQL becomes a leak vector.

    Checked against git itself rather than by parsing .gitignore, because
    parsing cannot resolve re-inclusion rules such as `!.gitkeep`.
    """
    import subprocess

    probe = subprocess.run(
        ["git", "check-ignore", "-q", "some-generated.log"],
        capture_output=True,
        cwd=pathlib.Path(__file__).resolve().parents[1],
    )
    assert probe.returncode == 0, (
        "*.log is not gitignored; a locally generated log can capture customer "
        "content via SQLAlchemy parameter echo and then be committed"
    )


def test_gitignore_keeps_a_path_for_placeholder_logs() -> None:
    """The blanket *.log rule must not block a tracked placeholder."""
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[1]
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        cwd=root,
    ).stdout.split()

    assert not any(name.endswith(".log") for name in untracked), (
        "an ignored rule for logs is missing: untracked .log files are visible "
        "to git and could be added accidentally"
    )


def test_no_log_file_is_currently_tracked() -> None:
    """A log committed before the ignore rule existed is still on disk."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "*.log"],
        capture_output=True,
        text=True,
        cwd=pathlib.Path(__file__).resolve().parents[1],
    ).stdout.split()

    assert not tracked, (
        f"tracked log files present: {tracked}. They may contain customer "
        "content; remove them and confirm history is clean."
    )
