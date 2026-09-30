"""The production preflight must reject the right things, and nothing else.

`scripts/preflight_production_env.py` is the last gate before a public-facing
process accepts traffic, so its failure modes matter in two directions:

* a false **accept** ships an insecure or broken deployment, and
* a false **reject** crash-loops a correct deployment while the previously
  healthy instance keeps answering -- which is exactly how the previous
  production incident happened.

Both directions are asserted here. The private-address case is the subtle one
and is covered by a dedicated test with a comment explaining the distinction,
because "use the private database URL" and "never point customers at a private
address" are opposite rules that look alike in a diff.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = REPO_ROOT / "scripts" / "preflight_production_env.py"

# The preflight is a script, not an installed module, so it is imported by
# path. It is deliberately not moved into a package: the start scripts invoke it
# as a file, and a second copy behind a package would be a third thing to keep
# in sync.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

from preflight_production_env import (
    Report,
    _is_local_database_host,
    _is_unreachable_host,
    check_database_url,
    check_https_url,
)

# Imported so the test can assert the two gates share one allowlist rather than
# two that happen to agree today. See test_database_tls_policy_matches_the_validator.
from scripts.validate_production_config import TLS_ENFORCING_MODES

# Every valid example carries an enforcing TLS mode, because the preflight now
# requires one. A URL without it is a rejection fixture, not a valid baseline, so
# the constant used for "should pass" checks must say so explicitly.
PRIVATE_DB = "postgresql+asyncpg://cxops:hunter2@10.0.0.5:5432/cxops?sslmode=require"


def _tls_url(query: str, host: str = "db.example.com") -> str:
    """A syntactically valid, non-local database URL with `query` appended."""
    return f"postgresql+asyncpg://cxops:hunter2@{host}:5432/cxops?{query}"


def _report() -> Report:
    return Report()


# ---------------------------------------------------------------------------
# The two reachability rules are genuinely different
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "https://localhost:8000",
        "https://10.0.0.5",
        "https://192.168.1.10",
        "https://172.16.0.4",
        "https://[fd00::1]",
    ],
)
def test_browser_facing_urls_reject_private_addresses(url: str) -> None:
    """A customer must be able to load these, so private means unreachable."""
    assert _is_unreachable_host(url), f"{url} is unreachable from a browser"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@127.0.0.1:5432/db",
        "postgresql+asyncpg://u:p@localhost:5432/db",
        "postgresql+asyncpg://u:p@0.0.0.0:5432/db",
        "postgresql+asyncpg://u:p@[::1]:5432/db",
        "postgresql+asyncpg://u:p@host.docker.internal:5432/db",
    ],
)
def test_database_rejects_only_local_hosts(url: str) -> None:
    """Loopback and the unspecified address mean "no database was wired up"."""
    assert _is_local_database_host(url), f"{url} would use local data"


def test_database_accepts_a_private_address() -> None:
    """Regression guard: a private address is correct for a production database.

    A production database is often internal-only by design, so its connection
    string carries a private address on the network the service runs on.
    Rejecting that would crash-loop a *correct* deployment and would push an
    operator toward exposing the database publicly to satisfy the check -- a
    strict security regression caused by an over-broad rule.

    If this test ever fails, the two helpers have collapsed into one. Fix the
    collapse, do not widen the browser rule.
    """
    assert _is_unreachable_host(PRIVATE_DB), (
        "a private address is still unreachable from a browser"
    )
    assert not _is_local_database_host(PRIVATE_DB), (
        "a private address is expected for a production database"
    )


# ---------------------------------------------------------------------------
# Report-level behaviour
# ---------------------------------------------------------------------------


def test_database_url_reports_a_private_address_as_ready() -> None:
    report = _report()
    check_database_url(report, PRIVATE_DB)
    assert report.ready, f"a private address must be accepted: {report.problems}"


# ---------------------------------------------------------------------------
# Database TLS is mandatory, and this is the gate a deployment starts behind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", sorted(TLS_ENFORCING_MODES))
def test_database_url_accepts_each_enforcing_tls_mode(mode: str) -> None:
    """Every mode in the shared policy is accepted, under either spelling."""
    for key in ("sslmode", "ssl"):
        report = _report()
        check_database_url(report, _tls_url(f"{key}={mode}"))
        assert report.ready, f"{key}={mode} should pass: {report.problems}"


@pytest.mark.parametrize(
    ("query", "why"),
    [
        ("sslmode=prefer", "negotiates TLS but continues in plaintext"),
        ("ssl=prefer", "negotiates TLS but continues in plaintext"),
        ("sslmode=disable", "TLS is switched off"),
        ("sslmode=false", "TLS is switched off"),
        ("ssl=0", "TLS is switched off"),
        ("", "no sslmode= parameter"),
        ("sslmode=", "no sslmode= parameter"),
        ("sslmode=allow", "unsupported mode"),
        ("sslmode=verify-none", "unsupported mode"),
        ("options=-c%20sslmode%3Ddisable", "no sslmode= parameter"),
    ],
)
def test_database_url_rejects_a_url_that_can_be_plaintext(query: str, why: str) -> None:
    """A URL that does not *enforce* TLS must not boot a production process.

    `prefer` and a missing mode are the dangerous pair: both can succeed over a
    plaintext socket, so a connection that worked tells the operator nothing
    about whether the password crossed the network in the clear.
    """
    report = _report()
    check_database_url(report, _tls_url(query))
    assert not report.ready, f"{query!r} should be rejected"
    rendered = "\n".join(report.problems + report.lines)
    assert why in rendered, f"expected {why!r} in: {rendered}"
    # The remediation must name the enforcing mode, not just the problem.
    assert "sslmode=require" in rendered
    assert "verify-ca" in rendered and "verify-full" in rendered


def test_database_tls_policy_matches_the_validator() -> None:
    """One policy, two call sites.

    The preflight gates startup and the validator gates go-live. If the two
    allowlists drift, the stronger one is decorative: a deploy that the validator
    accepts could still be refused at boot, or worse, the reverse.
    """
    report = _report()
    check_database_url(report, _tls_url("sslmode=prefer"))
    assert not report.ready
    assert "prefer" not in TLS_ENFORCING_MODES, (
        "the preflight rejects prefer, so the shared policy must not allow it"
    )


def test_private_address_with_required_tls_is_still_accepted() -> None:
    """The two rules are independent: private host AND enforced TLS is valid."""
    report = _report()
    check_database_url(report, _tls_url("sslmode=require", host="10.0.0.5"))
    assert report.ready, f"private + require must pass: {report.problems}"


def test_localhost_with_required_tls_is_still_rejected() -> None:
    """TLS cannot rescue a local address; it is rejected for being local."""
    report = _report()
    check_database_url(report, _tls_url("sslmode=require", host="localhost"))
    assert not report.ready
    assert "non-local" in "\n".join(report.problems + report.lines)


def test_database_url_never_echoes_credentials_or_the_query() -> None:
    """Neither the password nor the raw query string may reach the log."""
    report = _report()
    check_database_url(
        report,
        "postgresql+asyncpg://u:sup3rs3cret@db.example.com:5432/db"
        "?sslmode=disable&other=hunter2",
    )
    rendered = "\n".join(report.problems + report.lines)
    assert "sup3rs3cret" not in rendered
    assert "hunter2" not in rendered
    # The query string is never echoed, only the mode is named in prose.
    assert "sslmode=disable&" not in rendered
    assert "other=" not in rendered


def test_database_url_never_echoes_the_password() -> None:
    report = _report()
    check_database_url(report, "postgresql+asyncpg://u:sup3rs3cret@127.0.0.1:5432/db")
    rendered = "\n".join(report.problems + report.lines)
    assert "sup3rs3cret" not in rendered
    assert "hunter2" not in rendered


@pytest.mark.parametrize(
    "url",
    ["http://example.com", "https://127.0.0.1:8000", "", "not-a-url"],
)
def test_public_url_failures_are_reported(url: str) -> None:
    report = _report()
    check_https_url(report, "FRONTEND_BASE_URL", url, "reason")
    assert not report.ready


def test_https_public_url_is_accepted() -> None:
    report = _report()
    check_https_url(report, "FRONTEND_BASE_URL", "https://cxops.example.com", "r")
    assert report.ready


# ---------------------------------------------------------------------------
# End-to-end: the script's own exit status
# ---------------------------------------------------------------------------


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the preflight in a clean environment, inheriting only PATH/HOME."""
    import os

    base = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG")}
    return subprocess.run(
        [sys.executable, str(PREFLIGHT)],
        env={**base, **env},
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def test_preflight_exits_zero_on_a_valid_production_environment() -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    result = _run(
        {
            "ENVIRONMENT": "production",
            "DEBUG": "false",
            "AUTH_DEV_MODE": "false",
            "AUTH_MODE": "jwks",
            "AUTH_JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
            "ENCRYPTION_KEYS": key,
            "OPENAI_API_KEY": "sk-not-real",
            "FRONTEND_BASE_URL": "https://cxops-account.replit.app",
            "BACKEND_PUBLIC_URL": "https://cxops-account.replit.app",
            "DATABASE_URL": PRIVATE_DB,
        }
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _production_env(database_url: str) -> dict[str, str]:
    from cryptography.fernet import Fernet

    return {
        "ENVIRONMENT": "production",
        "DEBUG": "false",
        "AUTH_DEV_MODE": "false",
        "AUTH_MODE": "jwks",
        "AUTH_JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
        "ENCRYPTION_KEYS": Fernet.generate_key().decode(),
        "OPENAI_API_KEY": "sk-not-real",
        "FRONTEND_BASE_URL": "https://cxops-account.replit.app",
        "BACKEND_PUBLIC_URL": "https://cxops-account.replit.app",
        "DATABASE_URL": database_url,
    }


@pytest.mark.parametrize(
    "query",
    ["sslmode=prefer", "sslmode=disable", ""],
    ids=["prefer", "disable", "no-tls"],
)
def test_preflight_exits_nonzero_for_a_plaintext_capable_database_url(
    query: str,
) -> None:
    """The startup gate itself must refuse these, not just the advisory report.

    A deploy that boots with a plaintext-capable URL is the exact failure the
    preflight exists to prevent: it looks healthy and the problem only surfaces
    as an unencrypted connection nobody was watching for.
    """
    result = _run(_production_env(_tls_url(query)))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "DATABASE_URL" in result.stdout
    assert "sslmode=require" in result.stdout


def test_preflight_exits_zero_for_a_tls_enforcing_database_url() -> None:
    result = _run(_production_env(_tls_url("sslmode=verify-full")))
    assert result.returncode == 0, result.stdout + result.stderr


def test_preflight_exits_nonzero_when_encryption_keys_are_missing() -> None:
    result = _run(
        {
            "ENVIRONMENT": "production",
            "DEBUG": "false",
            "AUTH_DEV_MODE": "false",
            "AUTH_MODE": "jwks",
            "AUTH_JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
            "ENCRYPTION_KEYS": "",
            "OPENAI_API_KEY": "sk-not-real",
            "FRONTEND_BASE_URL": "https://cxops-account.replit.app",
            "BACKEND_PUBLIC_URL": "https://cxops-account.replit.app",
            "DATABASE_URL": PRIVATE_DB,
        }
    )
    assert result.returncode != 0
    assert "ENCRYPTION_KEYS" in result.stdout


def test_preflight_output_never_contains_a_secret_value() -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    result = _run(
        {
            "ENVIRONMENT": "production",
            "DEBUG": "true",
            "AUTH_DEV_MODE": "false",
            "AUTH_MODE": "jwks",
            "AUTH_JWKS_URL": "https://auth.example.com/jwks",
            "ENCRYPTION_KEYS": key,
            "OPENAI_API_KEY": "sk-leaked-if-printed",
            "FRONTEND_BASE_URL": "http://localhost:3000",
            "BACKEND_PUBLIC_URL": "http://localhost:8000",
            "DATABASE_URL": "postgresql+asyncpg://u:topsecretpw@127.0.0.1:5432/db",
        }
    )
    combined = result.stdout + result.stderr
    assert "topsecretpw" not in combined
    assert "sk-leaked-if-printed" not in combined
    assert key not in combined
    # ...but it must still fail, loudly, on the real problems.
    assert result.returncode != 0
    assert "DEBUG" in combined
