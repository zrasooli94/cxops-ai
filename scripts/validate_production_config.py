#!/usr/bin/env python3
"""Pre-deploy production configuration validation (Phase 1P.3).

Answer one question before a live pilot: *is this deployment configured such
that a customer could actually reach the widget, and is anything in it unsafe
to expose?*

    .venv/bin/python scripts/validate_production_config.py
    .venv/bin/python scripts/validate_production_config.py --strict
    .venv/bin/python scripts/validate_production_config.py --environment production

Every check prints exactly one of:

``PASS``
    The setting is correct.
``WARN``
    Not fatal, but an operator should look at it before going live.
``FAIL``
    The pilot must not go live. Fails the run.

Design rules:

* **Read-only.** The script never writes, never migrates, and never makes a
  network call to a tenant or provider.
* **No secret values, ever.** A setting that can hold credential material is
  reported as present/absent/valid. No URL containing a password is printed; a
  connection string is reduced to scheme, host, and whether TLS is requested.
* **Multiple problems per run.** All checks execute so one deploy surfaces
  every issue, not just the first.

Exit code is 1 if any check FAILed, 0 otherwise. With ``--strict``, a WARN
also fails the run.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import pydantic

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = REPO_ROOT / "config" / "tenants"

# The revision this build expects an operator's database to be at. Compared
# against both the repository's single head and the live ``alembic current``, so
# three distinct failures are separable:
#
#   * repository has no single head, or a different one than this
#   * database is behind the repository head
#   * database is at a revision this build does not know how to validate
#
# A drifted expectation is itself a deploy blocker: it means the deploy is not
# the code that was reviewed, so the rest of this report describes something
# other than what would run.
EXPECTED_ALEMBIC_HEAD = "1p4a0001"

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

_EXAMPLE_TLD_RE = re.compile(
    r"^https?://[a-z0-9.\-]*\.(example|test|invalid|localhost)(:\d+)?$",
    re.IGNORECASE,
)


class Reporter:
    def __init__(self, fail_on_warn: bool) -> None:
        self.fail_on_warn = fail_on_warn
        self.lines: list[str] = []
        self.failures = 0
        self.warnings = 0

    def record(self, status: str, check: str, detail: str = "") -> None:
        suffix = f" - {detail}" if detail else ""
        self.lines.append(f"{status:<4}  {check}{suffix}")
        if status == FAIL:
            self.failures += 1
        elif status == WARN:
            self.warnings += 1

    def ok(self, check: str, detail: str = "") -> None:
        self.record(PASS, check, detail)

    def warn(self, check: str, detail: str = "") -> None:
        self.record(WARN, check, detail)

    def fail(self, check: str, detail: str = "") -> None:
        self.record(FAIL, check, detail)

    def verdict(self) -> str:
        if self.failures:
            return "NOT READY"
        if self.warnings and self.fail_on_warn:
            return "NOT READY (--strict: warnings are fatal)"
        if self.warnings:
            return "READY WITH WARNINGS"
        return "READY"


def _describe_validation_error(exc: pydantic.ValidationError) -> str:
    """Summarise a pydantic failure without echoing any setting value.

    ``str(exc)`` embeds ``input_value={...}``, which is the entire settings
    dict - including every secret this script is careful never to print. Only
    the field path, error type, and our own message are reproduced.
    """
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ())) or "<root>"
        message = str(error.get("msg", "")).replace("\n", " ")
        parts.append(f"{location}: {message}")
    return "; ".join(parts) if parts else type(exc).__name__


def _load_settings(environment: str | None):
    """Build Settings with the requested environment, or return the failure."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from app.core.config import Settings  # type: ignore[import-not-found]
    except (ImportError, AttributeError) as exc:  # pragma: no cover
        return None, f"settings module not importable: {type(exc).__name__}"

    if environment:
        os.environ["ENVIRONMENT"] = environment
    try:
        return Settings(_env_file=REPO_ROOT / ".env"), None
    except pydantic.ValidationError as exc:
        return None, _describe_validation_error(exc)
    except (ValueError, OSError) as exc:
        return None, f"{type(exc).__name__} (re-run with ENVIRONMENT set to inspect)"


def _safe_url_shape(url: str) -> str:
    """Describe a URL without ever revealing a password or query secret."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    scheme = parts.scheme or "none"
    host = parts.hostname or "none"
    port = f":{parts.port}" if parts.port else ""
    tls = "tls=yes" if scheme == "https" else "tls=NO"
    creds = "credentials=present" if (parts.username or parts.password) else "credentials=none"
    return f"{scheme}://{host}{port} ({tls}, {creds})"


# Hosts that resolve only on the machine or network that serves them. A pilot
# embed pointed at one of these is unreachable from a real customer's browser.
_UNREACHABLE_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "[::1]",
        "host.docker.internal",
    }
)


def _is_unreachable_host(url: str) -> bool:
    """True when the URL host is loopback or RFC1918/ULA private.

    Accepts a bare host, an IPv4 literal, or a bracketed IPv6 literal. Anything
    that cannot be parsed is left to the surrounding https check rather than
    being guessed at here.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url if "//" in url else f"https://{url}")
        host = parts.hostname or ""
    except ValueError:
        return False

    if not host:
        return False
    if host.lower() in _UNREACHABLE_HOSTS:
        return True
    try:
        import ipaddress

        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def check_public_urls(cfg, reporter: Reporter) -> None:
    for label in ("frontend_base_url", "backend_public_url"):
        value = getattr(cfg, label, "")
        if not value:
            reporter.fail(f"{label}", "is not set; embed snippets and pilot links would be broken")
            continue
        if not value.startswith("https://"):
            reporter.fail(
                f"{label}",
                f"must use https in production ({_safe_url_shape(value)})",
            )
            continue
        if _EXAMPLE_TLD_RE.match(value):
            reporter.warn(
                f"{label}",
                "still points at a reserved example domain; the pilot will not be reachable",
            )
            continue
        # An https:// loopback or private-range URL is syntactically valid and
        # passes every check above, but no customer can reach 127.0.0.1 from
        # their own browser. An embed pointed here looks configured on every
        # dashboard and fails only when a real user loads it, which is exactly
        # the failure this gate exists to catch before launch.
        if _is_unreachable_host(value):
            reporter.fail(
                f"{label}",
                "points at a loopback or private address "
                f"({_safe_url_shape(value)}); it is unreachable from a customer's "
                "browser even though it is https",
            )
            continue
        reporter.ok(f"{label}", _safe_url_shape(value))


def check_auth(cfg, reporter: Reporter) -> None:
    if cfg.auth_dev_mode:
        reporter.fail("AUTH_DEV_MODE", "is enabled; development auth must be off in production")
    else:
        reporter.ok("AUTH_DEV_MODE", "off")

    if cfg.auth_mode == "hs256":
        reporter.fail("AUTH_MODE", "is hs256; production requires jwks")
    else:
        reporter.ok("AUTH_MODE", cfg.auth_mode)

    jwks_url = cfg.auth_jwks_url
    if not jwks_url:
        if cfg.auth_mode == "jwks":
            reporter.fail("AUTH_JWKS_URL", "is required when AUTH_MODE=jwks")
        else:
            reporter.warn("AUTH_JWKS_URL", "is not set")
    elif not jwks_url.startswith("https://"):
        reporter.fail("AUTH_JWKS_URL", "must use https")
    else:
        reporter.ok("AUTH_JWKS_URL", "https")


def check_secrets(cfg, reporter: Reporter) -> None:
    """Report presence only. Values are never printed."""
    from app.core.encryption import validate_encryption_keys

    if not cfg.encryption_keys:
        reporter.fail("ENCRYPTION_KEYS", "is not set; stored credentials would be unprotectable")
    else:
        try:
            keys = validate_encryption_keys(cfg.encryption_keys)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the run
            reporter.fail("ENCRYPTION_KEYS", f"present but invalid ({type(exc).__name__})")
        else:
            if not keys:
                reporter.fail("ENCRYPTION_KEYS", "contains no usable key")
            else:
                reporter.ok("ENCRYPTION_KEYS", f"present, {len(keys)} usable key(s)")

    if cfg.openai_api_key:
        reporter.ok("OPENAI_API_KEY", "present")
    else:
        reporter.fail(
            "OPENAI_API_KEY",
            "is not set; the public widget cannot answer a message without it",
        )

    # Optional integration secrets.
    #
    # An absent secret is only a problem when the integration is actually in
    # use. For the A1 pilot none of these integrations are enabled, and an
    # empty ``ticket_event_webhook_secret`` makes the webhook endpoint answer
    # 503 by design (app/api/deps.py) rather than accepting unsigned calls.
    # Warning about that would be noise, and because these were unconditional
    # WARNs they also made ``--strict`` unachievable for a tenant that does not
    # use Zendesk - which the runbook tells operators to run in CI.
    #
    # So: absent + integration not configured is a PASS (a complete
    # configuration, stated plainly); configured + secret missing is a WARN,
    # because that combination is a real misconfiguration.
    zendesk_configured = bool(getattr(cfg, "zendesk_subdomain", ""))

    for label in ("zendesk_webhook_secret", "zendesk_client_secret"):
        value = getattr(cfg, label, "")
        if value:
            reporter.ok(label, "present")
        elif zendesk_configured:
            reporter.warn(
                label,
                "absent while ZENDESK_SUBDOMAIN is set; Zendesk is configured "
                "but cannot authenticate",
            )
        else:
            reporter.ok(label, "not configured (integration disabled)")

    ticket_secret = getattr(cfg, "ticket_event_webhook_secret", "")
    if ticket_secret:
        reporter.ok("ticket_event_webhook_secret", "present")
    else:
        reporter.ok(
            "ticket_event_webhook_secret",
            "not configured (webhook disabled; the endpoint answers 503)",
        )


def check_database(cfg, reporter: Reporter) -> None:
    url = cfg.database_url
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    scheme = parts.scheme or ""
    host = parts.hostname or ""

    if not parts.hostname:
        reporter.fail("DATABASE_URL", "is not a usable PostgreSQL URL")
        return
    if "postgresql" not in scheme:
        reporter.fail("DATABASE_URL", f"scheme must be postgresql (got {scheme or 'none'})")
    if parts.username or parts.password:
        reporter.ok("DATABASE_URL", f"{_safe_url_shape(url)} (credentials present, values hidden)")
    else:
        reporter.ok("DATABASE_URL", _safe_url_shape(url))

    if host in ("localhost", "127.0.0.1", "::1"):
        reporter.fail("DATABASE_URL", "points at localhost; production must use a managed database")

    # Settings.normalize_database_url rewrites ``sslmode=`` to ``ssl=``, so both
    # spellings have to be understood here.
    query = (parts.query or "").lower()
    tls_params = dict(
        pair.split("=", 1) for pair in query.split("&") if "=" in pair
    )
    tls = tls_params.get("ssl", tls_params.get("sslmode", ""))
    if tls in ("disable", "false", "0"):
        reporter.fail("DATABASE_URL", "has TLS disabled; production traffic must be encrypted")
    elif tls in ("require", "verify-ca", "verify-full", "true", "1", "prefer"):
        reporter.ok("DATABASE_URL", f"requests TLS (ssl={tls})")
    else:
        reporter.warn(
            "DATABASE_URL",
            "does not request TLS; set sslmode=require if the database is off-platform",
        )


def check_runtime_flags(cfg, reporter: Reporter) -> None:
    if cfg.debug:
        # The wording must stay true when ENVIRONMENT is not yet production:
        # the ENVIRONMENT check warns separately, and a wrong claim here would
        # teach an operator to distrust the report.
        reporter.fail("DEBUG", "is on; a production deploy must set DEBUG=false")
    else:
        reporter.ok("DEBUG", "off")

    if cfg.environment != "production":
        reporter.warn(
            "ENVIRONMENT",
            f"is {cfg.environment!r}; production rules were still applied",
        )
    else:
        reporter.ok("ENVIRONMENT", "production")


def check_migrations(reporter: Reporter) -> None:
    """Read-only migration check: compare ``current`` to ``heads``, never upgrade."""
    import subprocess

    alembic_ini = REPO_ROOT / "alembic.ini"
    if not alembic_ini.exists():
        reporter.fail("ALEMBIC", "alembic.ini not found")
        return

    def run(*args: str) -> tuple[int, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(alembic_ini), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
            timeout=90,
            check=False,
        )
        # Revisions go to stdout; alembic's INFO logging goes to stderr. Only
        # stdout is parsed, so log lines can never be mistaken for a revision.
        return proc.returncode, proc.stdout.strip()

    # A revision id is whatever the migration author chose, conventionally hex but
    # not required to be: this repository's head is "1p4a0001", which contains
    # non-hex letters. Accept the `alembic heads`/`current` line shape and nothing
    # else, so a database one revision behind is a FAIL rather than a warning.
    _REVISION_LINE = re.compile(r"^(?P<rev>[0-9A-Za-z][0-9A-Za-z_]*)(?:\s+\(head\))?$")

    def revision_ids(text: str) -> set[str]:
        found: set[str] = set()
        for line in text.splitlines():
            match = _REVISION_LINE.match(line.strip())
            if match:
                found.add(match.group("rev"))
        return found

    # --- Step 1: this build's own expectation must match the repository. ---
    # Checked before touching the database so a stale constant is reported as
    # the build problem it is, not as a database problem.
    code_heads, heads = run("heads")
    if code_heads != 0:
        reporter.fail("ALEMBIC heads", "could not resolve migration history")
        return
    head_ids = revision_ids(heads)
    if not head_ids:
        reporter.fail("ALEMBIC heads", "no revisions reported; migration history is unreadable")
        return
    if len(head_ids) > 1:
        reporter.fail(
            "ALEMBIC heads",
            f"multiple heads ({', '.join(sorted(head_ids))}); merge the branches "
            "before deploying",
        )
        return
    repo_head = next(iter(head_ids))
    if repo_head != EXPECTED_ALEMBIC_HEAD:
        reporter.fail(
            "ALEMBIC heads",
            f"repository head is {repo_head!r} but this validator expects "
            f"{EXPECTED_ALEMBIC_HEAD!r}; update EXPECTED_ALEMBIC_HEAD in the same "
            "commit that adds the migration",
        )
        return

    # --- Step 2: the database must be at the head. ---
    code, current = run("current")
    if code != 0:
        reporter.fail("ALEMBIC current", "could not resolve; is the database reachable?")
        return
    current_ids = revision_ids(current)

    if not current_ids:
        reporter.fail(
            "ALEMBIC current",
            "reports no applied revision on an initialised schema table; run "
            "'scripts/run_migrations.sh'",
        )
        return

    # Classify against the full revision history, not just the head. Comparing
    # current to head alone cannot tell "one revision behind" (expected, fixable
    # by running the migration) from "at a revision this build has never heard
    # of" (a schema mismatch no migration run can fix), and reporting the second
    # as the first would send an operator to re-run a migration pointlessly.
    #
    # The history is read in-process from the repository's own migration scripts
    # rather than by parsing `alembic history` output. That output is
    # human-formatted ("1p4a0001 -> 1p2a0001 (head)") and its exact shape varies
    # across Alembic versions; parsing it would be a fragile way to learn
    # something the ScriptDirectory already knows exactly.
    known_ids: set[str] | None = None
    if alembic_ini.exists():
        try:
            from alembic.config import Config as _AlembicConfig
            from alembic.script import ScriptDirectory

            _script = ScriptDirectory.from_config(_AlembicConfig(str(alembic_ini)))
            known_ids = set(_script.get_revisions("heads"))
            for _rev in _script.walk_revisions():
                known_ids.add(_rev.revision)
        except Exception:  # noqa: BLE001 - fall back to a weaker check
            known_ids = None

    if known_ids:
        unknown = current_ids - known_ids
        if unknown:
            reporter.fail(
                "ALEMBIC",
                f"database is at {', '.join(sorted(unknown))}, which is not in this "
                f"build's migration history (head {repo_head}); the deploy does not "
                "match the schema",
            )
            return

    missing = head_ids - current_ids
    if missing:
        reporter.fail(
            "ALEMBIC",
            f"migration gap: {', '.join(sorted(missing))} not applied "
            "(run 'alembic upgrade head' from a single deploy step, then "
            "re-run scripts/run_migrations.sh)",
        )
        return

    reporter.ok("ALEMBIC", f"at expected head ({repo_head})")


def check_manifests(reporter: Reporter) -> None:
    """Re-validate every tenant manifest, including the secret scan."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from app.tenant_onboarding import (  # type: ignore[import-not-found]
            ManifestError,
            load_manifest_file,
        )
    except (ImportError, AttributeError) as exc:  # pragma: no cover
        reporter.fail("TENANT MANIFESTS", f"onboarding module not importable: {exc}")
        return

    if not MANIFEST_DIR.is_dir():
        reporter.warn("TENANT MANIFESTS", f"{MANIFEST_DIR} does not exist")
        return

    paths = sorted(MANIFEST_DIR.glob("*.yaml")) + sorted(MANIFEST_DIR.glob("*.yml"))
    if not paths:
        reporter.warn("TENANT MANIFESTS", "no manifests found")
        return

    for path in paths:
        try:
            manifest = load_manifest_file(path)
        except ManifestError as exc:
            reporter.fail(f"MANIFEST {path.name}", f"{len(exc.problems)} problem(s): {exc.problems[0]}")
            continue

        if manifest.public_chat is not None and manifest.public_chat.enabled:
            for origin in manifest.public_chat.allowed_origins:
                if _EXAMPLE_TLD_RE.match(origin):
                    reporter.warn(
                        f"MANIFEST {path.name}",
                        f"origin {origin} is a reserved example domain; the pilot will not be reachable",
                    )
                    break
            else:
                reporter.ok(
                    f"MANIFEST {path.name}",
                    f"{manifest.slug}: widget enabled, {len(manifest.public_chat.allowed_origins)} origin(s)",
                )
        elif manifest.public_chat is not None:
            reporter.ok(
                f"MANIFEST {path.name}",
                f"{manifest.slug}: widget disabled (staging posture)",
            )
        else:
            reporter.ok(f"MANIFEST {path.name}", f"{manifest.slug}: no public_chat block")

        for integration in manifest.business_integrations:
            if integration.provider_mode != "local_demo":
                reporter.fail(
                    f"MANIFEST {path.name}",
                    f"provider {integration.provider} declares mode "
                    f"{integration.provider_mode!r}; this build ships only local_demo, "
                    "so a live mode would misrepresent simulated data as real",
                )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate production configuration for the live pilot.",
    )
    parser.add_argument(
        "--environment",
        default=None,
        help="Evaluate as this ENVIRONMENT value (e.g. production).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures.",
    )
    parser.add_argument(
        "--skip-migrations",
        action="store_true",
        help="Skip the database-backed alembic check (for air-gapped runs).",
    )
    args = parser.parse_args()

    reporter = Reporter(fail_on_warn=args.strict)

    print("CXOps production configuration validation")
    print(f"  environment : {args.environment or '(from .env / process env)'}")
    print(f"  strict      : {args.strict}")
    print()

    cfg, error = _load_settings(args.environment)
    if cfg is None:
        reporter.fail("SETTINGS", f"failed to load: {error}")
    else:
        check_runtime_flags(cfg, reporter)
        check_public_urls(cfg, reporter)
        check_auth(cfg, reporter)
        check_secrets(cfg, reporter)
        check_database(cfg, reporter)

    check_manifests(reporter)

    if args.skip_migrations:
        reporter.warn("ALEMBIC", "skipped by --skip-migrations")
    else:
        check_migrations(reporter)

    for line in reporter.lines:
        print(f"  {line}")

    print()
    print(
        f"summary: {reporter.failures} failure(s), {reporter.warnings} warning(s)"
    )
    print(f"READY: {reporter.verdict()}")
    return 1 if reporter.failures or (reporter.fail_on_warn and reporter.warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
