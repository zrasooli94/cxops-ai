#!/usr/bin/env python3
"""Non-destructive, read-only live-demo readiness check.

Checks, without ever running a migration or writing to any store:

1. The CXOps backend separates liveness from readiness: ``GET /health`` proves
   the process is serving, ``GET /ready`` proves the database path works with a
   ``SELECT 1`` (503 when it does not), and ``GET /version`` confirms the build
   identity that is actually deployed.
2. The frontend answers its entry-point and lets the browser form a session
   (any 2xx/3xx response counts - an unauthenticated 200 or a redirect to /login
   are both healthy).
3. Backend configuration initializes under the current environment and passes
   production validation rules where they apply (dev-auth / hs256 flags are
   rejected in production, encryption keys must be present, ...).
4. Alembic can resolve its migration history. ``current`` and ``heads`` are
   both read-only (no ``upgrade`` / ``downgrade`` is ever issued here); a
   migration gap is reported and fails the check.

The script never prints secrets: configuration values that embed credentials
(database_url, encryption_keys, OAuth secrets, API keys) are reported only as
present/absent/valid booleans.

Exit code 0 when every check passes, 1 otherwise. Safe to run against a live
demo as often as needed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pydantic

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:3000"

SENSITIVE_CONFIG_KEYS = {
    "database_url",
    "auth_jwt_secret",
    "auth_jwks_url",
    "zendesk_webhook_secret",
    "ticket_event_webhook_secret",
    "zendesk_client_id",
    "zendesk_client_secret",
    "encryption_keys",
    "openai_api_key",
}

# In production these must be present. Settings already rejects several of them
# at construction time, but the ones it permits as optional (a webhook secret for
# an integration that is switched off, for example) would otherwise be reported
# as an innocuous "note" by a script whose whole job is to block a launch. A
# "note" that cannot block anything is not a check.
REQUIRED_IN_PRODUCTION = {
    "database_url",
    "auth_jwks_url",
    "encryption_keys",
    "openai_api_key",
}


def _http_json(url: str, timeout: float) -> tuple[int, dict]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    try:
        return response.status, json.loads(body)
    except (ValueError, OSError) as exc:
        raise urllib.error.URLError(f"non-JSON response from {url}: {exc}")


def check_backend_health(
    backend_url: str,
    timeout: float,
    results: list[str],
) -> bool:
    """Check liveness, then readiness, then build identity.

    ``/health`` is liveness only and never touches the database, so it cannot
    answer "is this instance actually able to serve traffic". ``/ready`` is the
    load-balancer contract: 200 only when PostgreSQL answers SELECT 1, 503
    otherwise. Probing readiness is what proves the database path really works.
    """
    try:
        status, payload = _http_json(f"{backend_url}/health", timeout)
    except urllib.error.HTTPError as exc:
        results.append(f"FAIL  backend {backend_url}/health -> HTTP {exc.code}")
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        results.append(f"FAIL  backend {backend_url}/health unreachable: {exc}")
        return False

    if status != 200:
        results.append(f"FAIL  backend /health returned HTTP {status}")
        return False
    if payload.get("status") != "healthy":
        results.append(f"FAIL  backend /health payload unexpected: {payload!r}")
        return False
    results.append("ok    backend /health answers 200 (liveness, no database check)")

    # Readiness: 503 is a legitimate, actionable answer, so an HTTPError carries
    # a response body we can read instead of collapsing straight to FAIL.
    try:
        ready_status, ready_payload = _http_json(f"{backend_url}/ready", timeout)
    except urllib.error.HTTPError as exc:
        results.append(f"FAIL  backend /ready -> HTTP {exc.code}")
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        results.append(f"FAIL  backend /ready unreachable: {exc}")
        return False

    if ready_status != 200:
        results.append(
            f"FAIL  backend /ready returned HTTP {ready_status} "
            f"(status={ready_payload.get('status')!r}); not eligible for traffic"
        )
        return False
    if ready_payload.get("status") != "ready" or ready_payload.get("checks", {}).get("database") != "ok":
        results.append(f"FAIL  backend /ready payload unexpected: {ready_payload!r}")
        return False
    results.append("ok    backend /ready answers 200; database select works")

    try:
        version_status, version_payload = _http_json(f"{backend_url}/version", timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        version_status, version_payload = 0, {}

    if version_status == 200 and version_payload.get("version"):
        results.append(
            f"ok    backend /version reports {version_payload.get('environment')} "
            f"version {version_payload['version']}"
        )
    else:
        results.append("WARN  backend /version did not answer 200; build identity unconfirmed")

    return True


def check_frontend(
    frontend_url: str,
    timeout: float,
    results: list[str],
) -> bool:
    # /login is the first renderable, cookie-routing page: a 2xx (already
    # session-less render) or 3xx (Auth-forwarding handler redirect) proves the
    # Next.js dynamic renderer is up. The page is never POSTed to.
    url = f"{frontend_url}/login"
    try:
        request = urllib.request.Request(url, method="GET", headers={
            "User-Agent": "cxops-readiness-check",
        })
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except (urllib.error.URLError, OSError) as exc:
        results.append(f"FAIL  frontend {url} unreachable: {exc}")
        return False

    if 200 <= code < 500 and code != 404:
        results.append(
            f"ok    frontend {url} answers HTTP {code} "
            f"({'redirect' if 300 <= code < 400 else 'render'})"
        )
        return True
    results.append(f"FAIL  frontend {url} -> HTTP {code}")
    return False


def check_config(results: list[str]) -> bool:
    """Build the pydantic Settings once under the process environment."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from app.core.config import Settings  # type: ignore[import-not-found]
    except (ImportError, AttributeError) as exc:
        results.append(f"FAIL  backend settings module importable: {exc}")
        return False

    try:
        cfg = Settings(_env_file=REPO_ROOT / ".env")
    except (pydantic.ValidationError, ValueError, OSError) as exc:
        results.append(f"FAIL  backend settings invalid: {exc}")
        return False

    environment = cfg.environment
    results.append(f"ok    backend settings valid (environment={environment})")

    production = environment == "production"
    missing_required = False
    for key in sorted(SENSITIVE_CONFIG_KEYS):
        value = getattr(cfg, key, "")
        if not value and production and key in REQUIRED_IN_PRODUCTION:
            results.append(f"FAIL  {key}: required in production but absent")
            missing_required = True
        else:
            results.append(f"note  {key}: {'present' if value else 'absent'}")

    # A recorded FAIL has to fail the check. Returning True here let the script
    # print FAIL lines and still report a clean run, so a missing production
    # secret was easy to miss when scanning the output.
    return not missing_required


def check_alembic(results: list[str]) -> bool:
    alembic_ini = REPO_ROOT / "alembic.ini"
    if not alembic_ini.exists():
        results.append("FAIL  alembic.ini not found")
        return False

    def run(*args: str) -> tuple[int, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(alembic_ini), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
            timeout=60,
            check=False,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    code, current = run("current")
    if code != 0:
        results.append(f"FAIL  alembic current could not resolve: {current[-400:]}")
        return False

    code_heads, heads = run("heads")
    if code_heads != 0:
        results.append(f"FAIL  alembic heads could not resolve: {heads[-400:]}")
        return False

    # Parse the revision ids out of the (single-line or multi-line) current
    # output. "alembic current" prints one revision per line; "(head)" marks the
    # latest applied migration. Only compare creation, never run an upgrade.
    # Alembic INFO/context log lines are ignored.
    #
    # Revision ids are NOT hex. This repository's alembic file_template
    # produces ids like "1p4a0001", which contain a letter outside [0-9a-f], so
    # a hex-only filter discards every real revision and reports "(none)". That
    # failure was silent: the checks below compare two empty sets, find no gap,
    # and return success. An unparseable migration state must fail, not pass.
    def revision_ids(output: str) -> set[str]:
        ids: set[str] = set()
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split()
            token = parts[0].strip("()")
            if not token:
                continue
            if not all(c.isalnum() or c == "_" for c in token) or token.isdigit():
                continue
            # "alembic current"/"heads" print a bare revision id, optionally
            # followed by "(head)". Nothing else. Requiring that exact shape is
            # what keeps the INFO handler's own log lines -- which begin with a
            # bare "INFO" token and pass an alphanumeric test -- out of the set.
            remainder = " ".join(parts[1:])
            if remainder and not remainder.startswith("(head"):
                continue
            ids.add(token)
        return ids

    current_ids = revision_ids(current)
    head_ids = revision_ids(heads)

    if not head_ids:
        results.append(
            "FAIL  could not parse any repository head from 'alembic heads' "
            f"output: {(heads or '(empty)')[:200]}"
        )
        return False
    if not current_ids:
        results.append(
            "FAIL  could not parse any applied revision from 'alembic current' "
            f"output: {(current or '(empty)')[:200]}"
        )
        return False

    results.append(
        f"ok    alembic current: {', '.join(sorted(current_ids)) or '(none)'}"
    )
    results.append(f"ok    alembic heads:  {', '.join(sorted(head_ids)) or '(none)'}")

    missing = head_ids - current_ids
    if missing:
        results.append(
            f"FAIL  migration gap: {', '.join(sorted(missing))} not applied "
            "(run 'alembic upgrade head' outside this check)"
        )
        return False
    results.append("ok    migrations at head")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("BACKEND_API_URL", DEFAULT_BACKEND_URL),
    )
    parser.add_argument(
        "--frontend-url",
        default=os.environ.get("FRONTEND_URL", DEFAULT_FRONTEND_URL),
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    results: list[str] = []
    checks: list[bool] = [
        check_backend_health(args.backend_url, args.timeout, results),
        check_frontend(args.frontend_url, args.timeout, results),
        check_config(results),
        check_alembic(results),
    ]

    print("CXOps Live Demo Readiness Check")
    print(f"backend : {args.backend_url}")
    print(f"frontend: {args.frontend_url}")
    print()
    for line in results:
        print(f"  {line}")

    ok = all(checks)
    print()
    print("READY: YES" if ok else "READY: NO")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())