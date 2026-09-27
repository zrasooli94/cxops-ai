"""Tests for scripts/validate_production_config.py (Phase 1P.3).

The script is an operator gate, so its own failure modes matter as much as its
checks: a validator that crashes, mislabels warnings as success, or prints a
secret is worse than no validator, because a green run becomes false assurance.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "validate_production_config.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_production_config", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load_module()


def _reporter(*, strict: bool = False) -> validator.Reporter:
    return validator.Reporter(fail_on_warn=strict)


def Fernet_key() -> str:
    """A valid single Fernet key, for tests that need a real secret value."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


# ----------------------------------------------------------------------
# Reporter semantics
# ----------------------------------------------------------------------


def test_reporter_records_three_states():
    reporter = _reporter()
    reporter.ok("check ok")
    reporter.warn("check warned")
    reporter.fail("check failed")

    assert reporter.failures == 1
    assert reporter.warnings == 1
    assert any(line.startswith("PASS") for line in reporter.lines)
    assert any(line.startswith("WARN") for line in reporter.lines)
    assert any(line.startswith("FAIL") for line in reporter.lines)


def test_verdict_never_reports_ready_when_something_failed():
    reporter = _reporter()
    reporter.fail("boom")

    assert reporter.verdict() == "NOT READY"


def test_strict_mode_promotes_warnings_to_a_failed_verdict():
    lenient = _reporter(strict=False)
    lenient.warn("reserved example domain")
    strict = _reporter(strict=True)
    strict.warn("reserved example domain")

    assert lenient.verdict() == "READY WITH WARNINGS"
    assert strict.verdict().startswith("NOT READY")


def test_clean_run_is_ready():
    reporter = _reporter()
    reporter.ok("everything")

    assert reporter.verdict() == "READY"


# ----------------------------------------------------------------------
# URL shape and public URL checks
# ----------------------------------------------------------------------


def test_safe_url_shape_never_leaks_credentials():
    rendered = validator._safe_url_shape(
        "postgresql+asyncpg://cxops:hunter2@db.internal:5432/cxops?sslmode=require"
    )

    assert "hunter2" not in rendered
    assert "cxops:hunter2" not in rendered
    assert "db.internal" in rendered
    assert "5432" in rendered


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "",
        "postgresql+asyncpg://",
    ],
)
def test_safe_url_shape_survives_malformed_input(url: str):
    # A validator must not raise while describing a bad value; that is exactly
    # the situation an operator needs it to explain.
    assert isinstance(validator._safe_url_shape(url), str)


def test_settings_model_refuses_http_public_urls_in_production():
    """The https requirement is enforced at construction, not only by the check.

    A process that boots with an http frontend URL must not start at all, so a
    misconfigured deploy fails closed instead of serving mixed content.
    """
    import pydantic

    with pytest.raises(pydantic.ValidationError) as excinfo:
        _settings_with(
            ENVIRONMENT="production",
            AUTH_MODE="jwks",
            AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
            FRONTEND_BASE_URL="http://www.example.com",
            BACKEND_PUBLIC_URL="https://api.example.com",
        )
    assert "FRONTEND_BASE_URL" in str(excinfo.value)


def test_settings_model_refuses_hs256_in_production():
    import pydantic

    with pytest.raises(pydantic.ValidationError) as excinfo:
        _settings_with(
            ENVIRONMENT="production",
            AUTH_MODE="hs256",
            FRONTEND_BASE_URL="https://www.example.com",
            BACKEND_PUBLIC_URL="https://api.example.com",
        )
    assert "AUTH_MODE" in str(excinfo.value)


def test_settings_model_refuses_debug_in_production():
    """DEBUG=true in production must fail at construction.

    The architecture document promises this enforcement, and stack traces or
    echoed SQL reaching a third-party widget is the exact failure mode the
    pilot must not have.
    """
    import pydantic

    with pytest.raises(pydantic.ValidationError) as excinfo:
        _settings_with(
            ENVIRONMENT="production",
            AUTH_MODE="jwks",
            AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
            FRONTEND_BASE_URL="https://www.example.com",
            BACKEND_PUBLIC_URL="https://api.example.com",
            DEBUG="true",
        )
    assert "DEBUG" in str(excinfo.value)


def test_settings_model_allows_debug_outside_production():
    """The guard is production-only: local development still wants DEBUG."""
    cfg = _settings_with(ENVIRONMENT="development", DEBUG="true")
    assert cfg.debug is True


def test_production_settings_pass_url_and_auth_checks():
    cfg = _settings_with(
        ENVIRONMENT="production",
        AUTH_MODE="jwks",
        AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
    )
    reporter = _reporter()
    validator.check_public_urls(cfg, reporter)
    validator.check_auth(cfg, reporter)

    assert reporter.failures == 0, reporter.lines


def test_missing_openai_key_fails_the_widget_gate():
    """A missing OpenAI key must block the deploy.

    The key is genuinely required at runtime, and not because of any business
    provider: ``app/services/agent_workflow_service.py`` builds
    ``AgentWorkflowService()`` at module import and its ``__init__`` constructs
    ``ChatOpenAI(api_key=SecretStr(settings.openai_api_key))``. With an empty key
    that raises ``OpenAIError`` during import, so ``import app.main`` fails and the
    API never starts. See
    ``test_local_demo_provider_still_requires_the_agent_openai_key``.
    """
    cfg = _settings_with(
        ENVIRONMENT="production",
        AUTH_MODE="jwks",
        AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
        OPENAI_API_KEY="",
    )
    reporter = _reporter()
    validator.check_secrets(cfg, reporter)

    assert reporter.failures >= 1
    assert "OPENAI_API_KEY" in "\n".join(reporter.lines)


def test_local_demo_provider_still_requires_the_agent_openai_key():
    """A1's ``local_demo`` mode removes the *business* provider, not the LLM.

    ``local_demo`` swaps the A1 adapter for a deterministic in-database one
    (quote status, pickup availability). It does not remove the model: the widget's
    send-message path calls ``agent_workflow_service.analyze()`` unconditionally,
    and that module cannot even be imported without an OpenAI key. So a
    ``local_demo`` pilot still fails when the key is missing, and it fails for the
    OpenAI reason only when everything else is valid.
    """
    cfg = _settings_with(
        ENVIRONMENT="production",
        AUTH_MODE="jwks",
        AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
        OPENAI_API_KEY="",
    )
    reporter = _reporter()
    validator.check_secrets(cfg, reporter)

    # Nothing else in check_secrets may fail for a complete local_demo config, so
    # the OpenAI requirement is provably the only reason this one fails.
    assert "OPENAI_API_KEY" in "\n".join(reporter.lines)
    assert reporter.failures == 1, reporter.lines


def test_local_demo_config_with_an_openai_key_has_no_openai_failure():
    """The inverse: a local_demo pilot is not penalised once the key is present.

    This is the case the A1 pilot actually runs in, and it must come back clean.
    """
    cfg = _settings_with(
        ENVIRONMENT="production",
        AUTH_MODE="jwks",
        AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
        OPENAI_API_KEY="sk-test-" + "0" * 40,
    )
    reporter = _reporter()
    validator.check_secrets(cfg, reporter)

    assert reporter.failures == 0, reporter.lines
    assert "OPENAI_API_KEY - present" in "\n".join(reporter.lines)


def test_openai_requirement_is_pinned_to_the_import_time_llm_construction():
    """Guard the runtime dependency the validator is asserting.

    If someone makes the LLM lazy, the OpenAI requirement stops being a boot
    requirement, and this assertion is what makes that change a deliberate
    decision to revisit rather than a silent divergence between the gate and the
    application.
    """
    import inspect

    from app.services.agent_workflow_service import AgentWorkflowService

    assert "openai_api_key" in inspect.getsource(AgentWorkflowService.__init__)


def test_configured_openai_key_passes_without_being_printed():
    secret = "sk-test-" + "0" * 40
    cfg = _settings_with(
        ENVIRONMENT="production",
        AUTH_MODE="jwks",
        AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
        OPENAI_API_KEY=secret,
    )
    reporter = _reporter()
    validator.check_secrets(cfg, reporter)
    rendered = "\n".join(reporter.lines)

    assert secret not in rendered
    assert reporter.failures == 0, reporter.lines


def test_missing_and_present_openai_keys_are_independent_of_the_environment(
    monkeypatch,
):
    """The two OpenAI outcomes must not depend on the machine running the test.

    This is the CI regression: a runner exporting ``OPENAI_API_KEY`` used to make
    the missing-key case pass silently.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leaked-from-ci-environment")

    missing = _reporter()
    validator.check_secrets(
        _settings_with(
            ENVIRONMENT="production",
            AUTH_MODE="jwks",
            AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
            OPENAI_API_KEY="",
        ),
        missing,
    )
    present = _reporter()
    validator.check_secrets(
        _settings_with(
            ENVIRONMENT="production",
            AUTH_MODE="jwks",
            AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
            OPENAI_API_KEY="sk-test-" + "0" * 40,
        ),
        present,
    )

    assert "OPENAI_API_KEY" in "\n".join(missing.lines)
    assert present.failures == 0, present.lines


def test_validator_never_prints_any_secret_value():
    """Presence only, for every secret the gate inspects."""
    secrets = {
        "OPENAI_API_KEY": "sk-never-print-this-value",
        "ENCRYPTION_KEYS": Fernet_key(),
        "ZENDESK_WEBHOOK_SECRET": "zendesk-never-print",
        "TICKET_EVENT_WEBHOOK_SECRET": "ticket-never-print",
        "ZENDESK_CLIENT_SECRET": "client-never-print",
    }
    cfg = _settings_with(
        ENVIRONMENT="production",
        AUTH_MODE="jwks",
        AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
        **secrets,
    )
    reporter = _reporter()
    validator.check_secrets(cfg, reporter)
    rendered = "\n".join(reporter.lines)

    for label, value in secrets.items():
        assert value not in rendered, f"{label} value reached the report"
    # Presence is still reported, under the settings attribute name rather than
    # the env-var casing, so compare case-insensitively.
    lowered = rendered.lower()
    for label in secrets:
        assert label.lower() in lowered, f"{label} presence should still be reported"


def test_http_urls_fail_because_the_gate_always_applies_production_rules():
    """The script is a production gate, so it judges the target as production.

    Pointing it at a local environment is allowed, but a local http URL is
    still not a production-passing configuration and must not be reported READY.
    """
    cfg = _settings_with(
        FRONTEND_BASE_URL="http://127.0.0.1:3000",
        BACKEND_PUBLIC_URL="http://127.0.0.1:8000",
    )
    reporter = _reporter()
    validator.check_public_urls(cfg, reporter)

    assert reporter.failures == 2, reporter.lines
    assert "https" in "\n".join(reporter.lines)


def test_non_production_environment_is_warned_not_silently_accepted():
    cfg = _settings_with(ENVIRONMENT="staging", DEBUG=False)
    reporter = _reporter()
    validator.check_runtime_flags(cfg, reporter)

    assert reporter.failures == 0, reporter.lines
    assert reporter.warnings >= 1
    assert "staging" in "\n".join(reporter.lines)


# ----------------------------------------------------------------------
# Secret handling
# ----------------------------------------------------------------------


def _settings_with(**overrides):
    """Build Settings isolated from the repository .env *and* the process env.

    ``_env_file=None`` alone is not enough. pydantic-settings only stops reading
    the dotenv file; every unset field still falls through to ``os.environ``. So a
    CI runner exporting ``OPENAI_API_KEY`` (a common repo secret) satisfied the
    OpenAI check and the "missing key must fail" test passed or failed depending
    on the machine it ran on. Every secret this suite reasons about is therefore
    pinned explicitly here, and a caller must opt in to a value.
    """
    from cryptography.fernet import Fernet

    from app.core.config import Settings

    base = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/cxops",
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "z" * 32,
        "ENCRYPTION_KEYS": Fernet.generate_key().decode(),
        "ENVIRONMENT": "development",
        "DEBUG": False,
        "FRONTEND_BASE_URL": "https://www.example.com",
        "BACKEND_PUBLIC_URL": "https://api.example.com",
        # Pinned, not inherited: see the docstring above.
        "OPENAI_API_KEY": "",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_secrets_report_presence_without_printing_values():
    secret = "sk-do-not-print-me-0123456789"
    cfg = _settings_with(OPENAI_API_KEY=secret)
    reporter = _reporter()
    validator.check_secrets(cfg, reporter)
    rendered = "\n".join(reporter.lines)

    assert secret not in rendered
    assert "OPENAI_API_KEY" in rendered
    assert "present" in rendered


def test_settings_model_refuses_dev_auth_in_production():
    """Fails closed at construction: the process never boots with dev auth."""
    import pydantic

    with pytest.raises(pydantic.ValidationError) as excinfo:
        _settings_with(
            ENVIRONMENT="production",
            AUTH_MODE="jwks",
            AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
            AUTH_DEV_MODE=True,
        )
    assert "AUTH" in str(excinfo.value)


def test_open_debug_is_a_hard_fail_even_against_production_settings():
    """A production build with DEBUG on is a finding, not a warning.

    Settings now refuses to construct with DEBUG on in production, so this state
    is unreachable through normal boot. The check is still verified against a
    hand-built config: the validator is an operator gate that must classify this
    correctly even if a future refactor relaxes the constructor guard.
    """
    cfg = SimpleNamespace(environment="production", debug=True)
    reporter = _reporter()
    validator.check_runtime_flags(cfg, reporter)

    assert reporter.failures >= 1
    assert "DEBUG" in "\n".join(reporter.lines)


def test_debug_off_in_production_is_clean():
    cfg = _settings_with(
        ENVIRONMENT="production",
        AUTH_MODE="jwks",
        AUTH_JWKS_URL="https://issuer.example.com/.well-known/jwks.json",
        DEBUG=False,
    )
    reporter = _reporter()
    validator.check_runtime_flags(cfg, reporter)

    assert reporter.failures == 0, reporter.lines
    assert reporter.verdict() == "READY"


def test_jwks_production_requires_a_jwks_url():
    import pydantic

    with pytest.raises(pydantic.ValidationError) as excinfo:
        _settings_with(
            ENVIRONMENT="production",
            AUTH_MODE="jwks",
            AUTH_JWT_SECRET="z" * 32,
        )
    assert "AUTH_JWKS_URL" in str(excinfo.value) or "jwks" in str(excinfo.value)


# ----------------------------------------------------------------------
# Manifest re-validation
# ----------------------------------------------------------------------


def test_shipped_manifests_validate_without_failures():
    reporter = _reporter()
    validator.check_manifests(reporter)

    # The shipped A1 manifest uses a reserved example domain on purpose, so a
    # warning is expected; a hard failure would mean the manifest or the
    # validator regressed.
    assert reporter.failures == 0, reporter.lines
    assert reporter.warnings >= 1, "the reserved example origin should be flagged"
    assert any("a1-cash-for-cars" in line for line in reporter.lines)


def test_broken_manifest_is_reported_as_a_failure(tmp_path, monkeypatch):
    (tmp_path / "broken.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    monkeypatch.setattr(validator, "MANIFEST_DIR", tmp_path)

    reporter = _reporter()
    validator.check_manifests(reporter)

    assert reporter.failures >= 1
    assert "broken.yaml" in "\n".join(reporter.lines)


def test_manifest_with_a_non_demo_provider_mode_fails(tmp_path, monkeypatch):
    """A live provider mode must never validate: this build only ships demos."""
    (tmp_path / "live.yaml").write_text(
        """
schema_version: 1
tenant:
  slug: acme
  name: "Acme"
business_integrations:
  - provider: a1_cash_for_cars
    provider_mode: live
    config: {}
    enabled: true
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(validator, "MANIFEST_DIR", tmp_path)

    reporter = _reporter()
    validator.check_manifests(reporter)

    assert reporter.failures >= 1
    assert "local_demo" in "\n".join(reporter.lines)


# ----------------------------------------------------------------------
# CLI surface
# ----------------------------------------------------------------------


def test_help_exits_zero():
    with pytest.raises(SystemExit) as excinfo:
        sys.argv = ["validate_production_config.py", "--help"]
        validator.main()
    assert excinfo.value.code == 0


# ----------------------------------------------------------------------
# Migration drift detection
# ----------------------------------------------------------------------


def _fake_alembic(monkeypatch, *, current: str, heads: str, code: int = 0) -> None:
    """Replace the alembic subprocess with fixed stdout for current/heads."""

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if code != 0:
            return subprocess.CompletedProcess(cmd, code, stdout="", stderr="boom")
        subcommand = cmd[cmd.index("alembic") + 1 :]
        payload = current if "current" in subcommand else heads
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_migration_check_parses_revision_ids_containing_non_hex_letters(
    monkeypatch,
) -> None:
    """The repository head is '1p2a0001', which is not valid hex.

    A parser that only accepts hex silently reports "no revisions" and downgrades
    a database one migration behind to a warning.
    """
    _fake_alembic(monkeypatch, current="1p2a0001 (head)\n", heads="1p2a0001 (head)\n")
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures == 0, reporter.lines
    assert any("at head" in line for line in reporter.lines), reporter.lines


def test_migration_gap_is_a_failure_not_a_warning(monkeypatch) -> None:
    """An unapplied head must be a hard FAIL with the revision named."""
    _fake_alembic(monkeypatch, current="1a1b2c3d (head)\n", heads="1p2a0001 (head)\n")
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures >= 1, reporter.lines
    joined = "\n".join(reporter.lines)
    assert "1p2a0001" in joined
    assert "alembic upgrade head" in joined


def test_unreachable_database_fails_loudly(monkeypatch) -> None:
    _fake_alembic(monkeypatch, current="", heads="", code=1)
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures >= 1, reporter.lines
    assert "database reachable" in "\n".join(reporter.lines)


def test_alembic_log_lines_are_not_parsed_as_revisions(monkeypatch) -> None:
    """alembic logs to stderr; only stdout may be treated as revision output.

    An INFO line whose first token looks like a word must not create a phantom
    revision that hides a real gap.
    """
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        subcommand = cmd[cmd.index("alembic") + 1 :]
        target = "current" if "current" in subcommand else "heads"
        stdout = "1a1b2c3d (head)\n" if target == "current" else "1p2a0001 (head)\n"
        stderr = "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(subprocess, "run", fake_run)
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures >= 1, reporter.lines
    assert "1p2a0001" in "\n".join(reporter.lines)


def test_migrations_can_be_skipped_for_air_gapped_runs(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["validate_production_config.py", "--skip-migrations"]
    )
    called: list[str] = []
    monkeypatch.setattr(
        validator, "check_migrations", lambda reporter: called.append("migrations")
    )
    monkeypatch.setattr(validator, "check_manifests", lambda reporter: None)

    validator.main()

    assert called == [], "--skip-migrations must not reach the database"


# ----------------------------------------------------------------------
# Static guarantees about the script itself
# ----------------------------------------------------------------------


def test_script_never_reprs_a_secret_attribute():
    source = SCRIPT.read_text(encoding="utf-8")
    for attribute in (
        "openai_api_key",
        "auth_jwt_secret",
        "encryption_keys",
        "zendesk_client_secret",
    ):
        assert f"cfg.{attribute}!r" not in source
        assert f'settings.{attribute}"' not in source
        assert f"settings.{attribute}!r" not in source


def test_migration_check_only_issues_read_only_alembic_commands():
    """current/heads only: the validator must never mutate the schema."""
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    issued: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    issued.add(arg.value)

    assert issued == {"current", "heads"}, issued
    assert "upgrade" not in issued
    assert "downgrade" not in issued


def test_script_is_executable_with_a_shebang():
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!")
    assert os.access(SCRIPT, os.X_OK)
