"""Release-gate tests for ``scripts/check_live_demo_readiness.py``.

This script is one of the gates that decides whether a pilot may launch, so a
check inside it that cannot fail is worse than no check at all. Every test here
is therefore aimed at one question: *can this check report a problem?* A gate
that silently passes on unreadable input has to be caught before launch, not
discovered during it.
"""

from __future__ import annotations

import subprocess

import pytest

from scripts import check_live_demo_readiness as readiness

ALEMBIC_LOG_NOISE = (
    "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n"
    "INFO  [alembic.runtime.migration] Will assume transactional DDL.\n"
)


def _fake_alembic(monkeypatch, *, current: str, heads: str, code: int = 0):
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        subcommand = cmd[cmd.index("alembic") + 1 :]
        target = "current" if "current" in subcommand else "heads"
        text_out = current if target == "current" else heads
        return subprocess.CompletedProcess(cmd, code, stdout=text_out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


# ---------------------------------------------------------------------------
# Revision parsing
# ---------------------------------------------------------------------------


def test_alphanumeric_revision_ids_are_parsed(monkeypatch) -> None:
    """This repo's revision ids are alphanumeric, not hex.

    ``1p4a0001`` contains ``p``. A hex-only filter discards it, and because the
    check then compared two empty sets it reported success. The previous version
    of this test suite would have passed while the gate reported ``(none)``.
    """
    _fake_alembic(
        monkeypatch, current="1p4a0001 (head)\n", heads="1p4a0001 (head)\n"
    )
    results: list[str] = []

    assert readiness.check_alembic(results) is True
    assert "1p4a0001" in "\n".join(results)
    assert "(none)" not in "\n".join(results)


def test_alembic_log_lines_are_not_mistaken_for_revisions(monkeypatch) -> None:
    """The INFO handler's own output must not become a revision id."""
    _fake_alembic(
        monkeypatch,
        current=ALEMBIC_LOG_NOISE + "1p4a0001 (head)\n",
        heads=ALEMBIC_LOG_NOISE + "1p4a0001 (head)\n",
    )
    results: list[str] = []

    assert readiness.check_alembic(results) is True
    joined = "\n".join(results)
    assert "1p4a0001" in joined
    assert "INFO" not in joined, joined


# ---------------------------------------------------------------------------
# The gate must be able to fail
# ---------------------------------------------------------------------------


def test_unparseable_heads_fails_instead_of_passing(monkeypatch) -> None:
    """Empty ``heads`` output is an error, not an implicit success."""
    _fake_alembic(monkeypatch, current="", heads="")
    results: list[str] = []

    assert readiness.check_alembic(results) is False
    assert "FAIL" in "\n".join(results)


def test_unparseable_current_fails_instead_of_passing(monkeypatch) -> None:
    """A known head with no readable ``current`` is a failure.

    This is the exact vacuous-pass case: the old code computed an empty gap,
    found nothing missing, and returned True.
    """
    _fake_alembic(
        monkeypatch, current="", heads="1p4a0001 (head)\n"
    )
    results: list[str] = []

    assert readiness.check_alembic(results) is False
    joined = "\n".join(results)
    assert "FAIL" in joined
    assert "alembic current" in joined


def test_real_migration_gap_fails(monkeypatch) -> None:
    _fake_alembic(
        monkeypatch, current="1p2a0001 (head)\n", heads="1p4a0001 (head)\n"
    )
    results: list[str] = []

    assert readiness.check_alembic(results) is False
    joined = "\n".join(results)
    assert "migration gap" in joined
    assert "1p4a0001" in joined


def test_database_ahead_of_the_build_is_flagged(monkeypatch) -> None:
    """A revision the build does not know is a mismatch, not a pass."""
    _fake_alembic(
        monkeypatch, current="ffff9999 (head)\n", heads="1p4a0001 (head)\n"
    )
    results: list[str] = []

    assert readiness.check_alembic(results) is False
    assert "FAIL" in "\n".join(results)


def test_alembic_subprocess_failure_is_loud(monkeypatch) -> None:
    _fake_alembic(monkeypatch, current="", heads="", code=1)
    results: list[str] = []

    assert readiness.check_alembic(results) is False
    assert "could not resolve" in "\n".join(results)


# ---------------------------------------------------------------------------
# Secrets must never be printed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["openai_api_key", "encryption_keys", "auth_jwt_secret", "database_url"],
)
def test_secret_values_are_never_echoed(name: str) -> None:
    """The gate reports the *presence* of a secret, never its value."""
    results: list[str] = []

    readiness.check_config(results)

    matching = [line for line in results if name in line]
    assert matching, f"{name} should be reported: {results}"
    for line in matching:
        assert "present" in line or "absent" in line, line
        # A leaked value would carry a key prefix or a long opaque blob.
        assert "sk-" not in line, line
        assert "postgresql://" not in line, line


def test_no_config_line_contains_an_opaque_value() -> None:
    """Broad sweep: every config line is a presence note, nothing more."""

    results: list[str] = []
    readiness.check_config(results)

    for line in results:
        assert len(line) < 200, line
        for marker in ("sk-", "postgresql://", "://user:", "BEGIN "):
            assert marker not in line, line
    assert results


# ---------------------------------------------------------------------------
# Production escalates notes to failures
# ---------------------------------------------------------------------------


def test_production_escalates_a_missing_required_secret(monkeypatch) -> None:
    """A "note" that cannot block a launch is not a check.

    Run against production, a missing encryption key must be a hard failure --
    that is exactly the mistake this gate exists to catch, and reporting it as
    an informational note lets the operator walk straight past it.
    """

    class _Settings:
        environment = "production"
        database_url = "postgresql://u:p@db.internal:5432/app"
        auth_jwks_url = "https://issuer.example-real.com/.well-known/jwks.json"
        auth_jwt_secret = ""
        openai_api_key = ""
        encryption_keys = ""
        zendesk_client_id = ""
        zendesk_client_secret = ""
        zendesk_webhook_secret = ""
        ticket_event_webhook_secret = ""

    monkeypatch.setattr("app.core.config.Settings", lambda **_: _Settings())
    results: list[str] = []

    passed = readiness.check_config(results)

    joined = "\n".join(results)
    assert "FAIL  openai_api_key" in joined, joined
    assert "FAIL  encryption_keys" in joined, joined
    assert "note  openai_api_key" not in joined, joined
    # Printing a FAIL line is not enough: the verdict must also be False, or the
    # caller reports a clean readiness run over a broken configuration.
    assert passed is False, joined


def test_development_keeps_missing_secrets_as_notes(monkeypatch) -> None:
    """Local development must not be blocked by a production requirement."""

    class _Settings:
        environment = "development"
        database_url = ""
        auth_jwks_url = ""
        auth_jwt_secret = ""
        openai_api_key = ""
        encryption_keys = ""
        zendesk_client_id = ""
        zendesk_client_secret = ""
        zendesk_webhook_secret = ""
        ticket_event_webhook_secret = ""

    monkeypatch.setattr("app.core.config.Settings", lambda **_: _Settings())
    results: list[str] = []

    assert readiness.check_config(results) is True
    assert "FAIL" not in "\n".join(results)
    assert "note  encryption_keys: absent" in "\n".join(results)
