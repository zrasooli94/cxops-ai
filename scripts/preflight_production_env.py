#!/usr/bin/env python3
"""Fail-fast production environment preflight, run before any long-lived process.

Both long-lived entry points call this first:

* ``scripts/start_replit_web.sh``   - the web/API process
* ``scripts/start_replit_worker.sh`` - the worker process

It exists because of a hard-won operational lesson. On a platform where every
restart re-runs the start command, a misconfigured process that boots anyway
looks healthy on the first request and then fails in front of a customer, or
worse, serves a half-configured pilot. A crash loop is the better outcome: it is
visible, bounded, and cannot serve a widget. So this script refuses to let a
process start unless the configuration is genuinely production-shaped.

Design rules:

* **Vendor-neutral.** It checks *capabilities* (https, reachable, encryption
  present), never a hosting provider's name. The same script is valid for any
  platform that can supply environment variables.
* **Read-only.** No network call, no write, no migration.
* **No secret values, ever.** A setting that can hold credential material is
  reported as present/absent/valid, and URLs are reduced to scheme/host/port so a
  password embedded in a connection string can never reach the log.
* **Aggregate.** Every check runs, so one restart surfaces every problem.

Exit code 0 when the environment is safe to start, 1 otherwise.
"""

from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent

# Settings already rejects several of these at construction time. They are
# re-checked here anyway, because the failure mode this script exists to prevent
# is a process that starts with a configuration an operator did not intend, and
# the cheapest way to guarantee that is to state the expectation explicitly.
EXPECTED_ENVIRONMENT = "production"

_UNREACHABLE_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
)


class Report:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.lines: list[str] = []

    def ok(self, check: str, detail: str = "") -> None:
        self.lines.append(f"  ok   {check}{f' - {detail}' if detail else ''}")

    def fail(self, check: str, detail: str) -> None:
        self.problems.append(f"{check}: {detail}")
        self.lines.append(f"  FAIL {check} - {detail}")

    @property
    def ready(self) -> bool:
        return not self.problems


def _safe_url_shape(url: str) -> str:
    """Scheme/host/port only. Never the userinfo, path, or query."""
    parts = urlsplit(url)
    scheme = parts.scheme or "none"
    host = parts.hostname or "none"
    port = f":{parts.port}" if parts.port else ""
    creds = "credentials=present" if (parts.username or parts.password) else ""
    return f"{scheme}://{host}{port}{f' ({creds})' if creds else ''}"


def _is_unreachable_host(url: str) -> bool:
    """True when the host is loopback or RFC1918/ULA private.

    Such a URL is syntactically fine and https, so a naive check passes it, yet
    no customer can load it from their own browser. An embed pointed here reads
    as configured on every dashboard and fails only for a real visitor.

    This is the rule for URLs a *browser* has to reach. It is deliberately too
    strict for a database -- see `_is_local_database_host`.
    """
    try:
        host = urlsplit(url if "//" in url else f"https://{url}").hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    if host.lower() in _UNREACHABLE_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def _is_local_database_host(url: str) -> bool:
    """True when a PostgreSQL host could not be a managed database.

    Narrower than `_is_unreachable_host` on purpose, and the difference is
    load-bearing: a private address is *correct* for a database.

    A managed database on a deployment platform is internal-only. Its
    connection string points at a private address on the platform's own
    network, reachable only from deployments on the same account -- that is
    deliberate. A leaked `DATABASE_URL` is therefore not reachable from the
    public internet, and requiring a public endpoint instead would *reduce*
    security. So private addresses pass.

    What still fails is anything implying the database lives on this machine:
    loopback, the unspecified address, link-local, and the container-host
    aliases. Those are the "the operator forgot to wire the managed database"
    cases, where the process would start and then silently use local data.
    """
    try:
        host = urlsplit(url if "//" in url else f"https://{url}").hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    if host.lower() in _UNREACHABLE_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified or address.is_link_local


def check_https_url(report: Report, label: str, value: str, why: str) -> None:
    if not value:
        report.fail(label, f"is not set; {why}")
        return
    if not value.startswith("https://"):
        report.fail(label, f"must use https in production ({_safe_url_shape(value)})")
        return
    if _is_unreachable_host(value):
        report.fail(
            label,
            f"points at a loopback or private address ({_safe_url_shape(value)}); "
            "unreachable from a customer's browser even though it is https",
        )
        return
    report.ok(label, _safe_url_shape(value))


def check_database_url(report: Report, value: str) -> None:
    if not value:
        report.fail("DATABASE_URL", "is not set; the process cannot open a connection")
        return
    parts = urlsplit(value)
    if "postgresql" not in (parts.scheme or ""):
        report.fail(
            "DATABASE_URL",
            f"scheme must be postgresql (got {parts.scheme or 'none'})",
        )
        return
    host = parts.hostname or ""
    if not host:
        report.fail("DATABASE_URL", "is not a usable PostgreSQL URL")
        return
    if _is_local_database_host(value):
        report.fail(
            "DATABASE_URL",
            f"points at a local address ({_safe_url_shape(value)}); a production "
            "process must use a managed database. A private address on the "
            "platform's own network is expected and allowed - only loopback, "
            "the unspecified address, and link-local are rejected.",
        )
        return
    report.ok("DATABASE_URL", _safe_url_shape(value))


def check_encryption(report: Report) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from app.core.encryption import validate_encryption_keys
    except (ImportError, AttributeError) as exc:  # pragma: no cover
        report.fail("ENCRYPTION_KEYS", f"encryption module not importable: {exc}")
        return

    raw = os.environ.get("ENCRYPTION_KEYS", "")
    if not raw:
        report.fail(
            "ENCRYPTION_KEYS",
            "is not set; stored integration credentials would be unprotectable",
        )
        return
    try:
        keys = validate_encryption_keys(raw)
    except Exception as exc:  # noqa: BLE001 - report, never crash the preflight
        report.fail("ENCRYPTION_KEYS", f"present but invalid ({type(exc).__name__})")
        return
    if not keys:
        report.fail("ENCRYPTION_KEYS", "contains no usable key")
        return
    report.ok("ENCRYPTION_KEYS", f"present, {len(keys)} usable key(s)")


def run() -> int:
    report = Report()

    environment = os.environ.get("ENVIRONMENT", "")
    if environment != EXPECTED_ENVIRONMENT:
        report.fail(
            "ENVIRONMENT",
            f"must be {EXPECTED_ENVIRONMENT!r} for a production process "
            f"(got {environment or 'unset'!r})",
        )
    else:
        report.ok("ENVIRONMENT", environment)

    # Settings refuses to construct with DEBUG on, but the check is repeated so
    # the reason is legible in the deploy log rather than buried in a
    # pydantic traceback.
    if os.environ.get("DEBUG", "").lower() in ("1", "true", "yes", "on"):
        report.fail(
            "DEBUG",
            "is on; DEBUG=true leaks stack traces and SQL into responses and logs",
        )
    else:
        report.ok("DEBUG", "off")

    if os.environ.get("AUTH_DEV_MODE", "").lower() in ("1", "true", "yes", "on"):
        report.fail(
            "AUTH_DEV_MODE",
            "is on; development auth must never be enabled in production",
        )
    else:
        report.ok("AUTH_DEV_MODE", "off")

    check_https_url(
        report,
        "FRONTEND_BASE_URL",
        os.environ.get("FRONTEND_BASE_URL", ""),
        "the widget snippet embedded into a tenant site would be broken",
    )
    check_https_url(
        report,
        "BACKEND_PUBLIC_URL",
        os.environ.get("BACKEND_PUBLIC_URL", ""),
        "absolute links in staff-visible messages would be wrong",
    )
    check_database_url(report, os.environ.get("DATABASE_URL", ""))
    check_encryption(report)

    if not os.environ.get("OPENAI_API_KEY", ""):
        report.fail(
            "OPENAI_API_KEY",
            "is not set; a message could not be answered even in a handoff-first pilot",
        )
    else:
        report.ok("OPENAI_API_KEY", "present")

    # Constructing Settings is the authoritative check. Everything above is a
    # fast, legible pre-check; this proves the application agrees.
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from app.core.config import Settings

        Settings(_env_file=None)
    except Exception as exc:  # noqa: BLE001 - report, never crash the preflight
        report.fail("SETTINGS", f"application configuration rejected: {type(exc).__name__}")
        detail = getattr(exc, "errors", None)
        if callable(detail):
            for error in exc.errors():  # type: ignore[union-attr]
                location = ".".join(str(item) for item in error.get("loc", ()))
                # Only our own message and the field name: never input_value,
                # which is the whole settings dict including every secret.
                report.lines.append(f"       {location}: {error.get('msg', '')}")
    else:
        report.ok("SETTINGS", "application configuration accepted")

    print("CXOps production environment preflight")
    for line in report.lines:
        print(line)
    print()
    if report.ready:
        print("PREFLIGHT: READY")
        return 0
    print(f"PREFLIGHT: NOT READY ({len(report.problems)} problem(s))")
    for problem in report.problems:
        print(f"  - {problem}")
    return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
