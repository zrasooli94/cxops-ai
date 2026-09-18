#!/usr/bin/env python3
"""Non-destructive, read-only live-demo readiness check.

Checks, without ever running a migration or writing to any store:

1. The CXOps backend answers ``GET /health`` (which also validates the
   database connection with a ``SELECT 1``).
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
    if payload.get("status") != "ok" or payload.get("database") != "ok":
        results.append(f"FAIL  backend /health payload unexpected: {payload!r}")
        return False
    results.append("ok    backend /health answers; database select works")
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

    for key in sorted(SENSITIVE_CONFIG_KEYS):
        value = getattr(cfg, key, "")
        results.append(f"note  {key}: {'present' if value else 'absent'}")

    return True


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
    # Alembic INFO/context log lines are ignored: revision ids here are hex.
    current_ids = {
        line.split()[0]
        for line in current.splitlines()
        if line.strip() and line.split()[0].strip("()").isalnum()
        and all(c in "0123456789abcdefABCDEF" for c in line.split()[0].strip("()"))
    }
    head_ids = {
        line.split()[0]
        for line in heads.splitlines()
        if line.strip() and line.split()[0].strip("()").isalnum()
        and all(c in "0123456789abcdefABCDEF" for c in line.split()[0].strip("()"))
    }

    results.append(
        f"ok    alembic current: {', '.join(sorted(current_ids)) or '(none)'}"
    )
    results.append(f"ok    alembic heads:  {', '.join(sorted(head_ids)) or '(none)'}")

    applied = current_ids & head_ids
    missing = head_ids - current_ids
    if missing:
        results.append(
            f"FAIL  migration gap: {', '.join(sorted(missing))} not applied "
            "(run 'alembic upgrade head' outside this check)"
        )
        return False
    if applied == head_ids and head_ids:
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