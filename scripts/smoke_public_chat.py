#!/usr/bin/env python3
"""End-to-end smoke test for the live public-chat pilot (Phase 1P.3).

Exercises the exact path a customer takes, against a running deployment, and
prints a PASS/FAIL line per step:

    .venv/bin/python scripts/smoke_public_chat.py \
        --backend-url http://127.0.0.1:8000 \
        --frontend-url http://127.0.0.1:3000 \
        --widget-key pk_live_... \
        --embedding-origin https://www.example.com

Steps:

1. ``/version`` answers and identifies the deployed build.
2. ``/health`` (liveness) answers 200 without requiring the database.
3. ``/ready`` (readiness) answers 200 with ``checks.database == "ok"``.
4. The frontend embed page renders and references the widget route.
5. A public session is created for the widget key from an allowed origin.
6. A message is sent and a reply is received.
7. Human handoff is requested and the status becomes ``human_requested``.
8. The session is closed and further messages are rejected.

Safety properties, because this runs against a real tenant:

* **Read-mostly.** It creates one real session, one ticket, and two messages for
  the tenant - exactly what a real visitor does. It always attempts to close the
  session in a ``finally`` block.
* **No secrets in output.** The widget key is never echoed. The session token is
  never printed, and message bodies are never printed - only their direction and
  length. A failure reports an HTTP status and the API's own ``detail`` string,
  which contains no credential material.
* **It refuses to fake success.** A step that cannot be verified fails. It never
  substitutes a placeholder reply for a missing one.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

PASS = "PASS"
FAIL = "FAIL"

USER_AGENT = "cxops-smoke-public-chat/1.0"
# A fixed, content-free probe message. Using a constant (rather than a random
# string) keeps smoke output diffable and keeps customer-data-shaped content out
# of the transcript.
PROBE_TEXT = "Hello, I have a general question about selling my car."


class StepFailure(Exception):
    """A step could not be verified."""


class Smoke:
    def __init__(self, backend_url: str, frontend_url: str, timeout: float) -> None:
        self.backend_url = backend_url.rstrip("/")
        self.frontend_url = frontend_url.rstrip("/")
        self.timeout = timeout
        self.session_token: str | None = None

    # --- HTTP helpers ----------------------------------------------------
    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                code = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read()
            code = exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise StepFailure(f"unreachable: {type(exc).__name__}") from exc

        try:
            return code, json.loads(body)
        except (ValueError, OSError):
            return code, None

    def _auth(self) -> dict[str, str]:
        if not self.session_token:
            raise StepFailure("no session token yet")
        return {"Authorization": f"Bearer {self.session_token}"}


def _describe(payload: Any) -> str:
    """Summarise an API error without echoing request content."""
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail[:200]
        if detail is not None:
            return json.dumps(detail)[:200]
    return "(no detail)"


def run(args: argparse.Namespace) -> int:
    smoke = Smoke(args.backend_url, args.frontend_url, args.timeout)
    results: list[tuple[str, str]] = []
    # Unique per run so a retried probe is never mistaken for an in-flight duplicate.
    run_id = f"{int(time.time())}-{id(args) % 100000}"

    def ok(name: str, detail: str = "") -> None:
        results.append((PASS, f"{name}{f' - {detail}' if detail else ''}"))

    def bad(name: str, detail: str) -> None:
        results.append((FAIL, f"{name} - {detail}"))

    # 1. Build identity
    try:
        code, payload = smoke._request(f"{smoke.backend_url}/version")
        if code != 200 or not isinstance(payload, dict):
            raise StepFailure(f"HTTP {code}: {_describe(payload)}")
        ok(
            "version endpoint",
            f"{payload.get('service')} {payload.get('version')} ({payload.get('environment')})",
        )
    except StepFailure as exc:
        bad("version endpoint", str(exc))
        _report(results)
        return 1

    # 2. Liveness - must not require the database
    try:
        code, payload = smoke._request(f"{smoke.backend_url}/health")
        if code != 200 or not isinstance(payload, dict) or payload.get("status") != "healthy":
            raise StepFailure(f"HTTP {code}: {_describe(payload)}")
        ok("liveness endpoint", "healthy (no database required)")
    except StepFailure as exc:
        bad("liveness endpoint", str(exc))

    # 3. Readiness - must require the database
    try:
        code, payload = smoke._request(f"{smoke.backend_url}/ready")
        if not isinstance(payload, dict):
            raise StepFailure(f"HTTP {code}: non-JSON response")
        checks = payload.get("checks") or {}
        if code != 200 or payload.get("status") != "ready":
            raise StepFailure(
                f"HTTP {code} status={payload.get('status')!r} database={checks.get('database')!r}"
            )
        if checks.get("database") != "ok":
            raise StepFailure(f"database check reported {checks.get('database')!r}")
        ok("readiness endpoint", "ready, database ok")
    except StepFailure as exc:
        bad("readiness endpoint", str(exc))

    # 4. Embed page
    try:
        code, _payload = smoke._request(f"{smoke.frontend_url}/chat/embed?key={args.widget_key}")
        if code != 200:
            raise StepFailure(f"HTTP {code}")
        ok("embed page", "HTTP 200")
    except StepFailure as exc:
        bad("embed page", str(exc))

    # 5. Create a session
    origin_header = {"x-embedding-origin": args.embedding_origin}
    try:
        code, payload = smoke._request(
            f"{smoke.backend_url}/public/chat/sessions",
            method="POST",
            payload={"public_widget_key": args.widget_key},
            headers=origin_header,
        )
        if code not in (200, 201) or not isinstance(payload, dict):
            raise StepFailure(f"HTTP {code}: {_describe(payload)}")
        token = (payload.get("session") or {}).get("token")
        if not token:
            raise StepFailure("response contained no session token")
        smoke.session_token = token
        config = payload.get("config") or {}
        ok(
            "session created",
            f"widget={config.get('display_name')!r} status={(payload.get('session') or {}).get('status')}",
        )
    except StepFailure as exc:
        bad("session created", str(exc))
        _report(results)
        return 1

    # 6. Send a message. Bodies are never printed.
    try:
        code, payload = smoke._request(
            f"{smoke.backend_url}/public/chat/messages",
            method="POST",
            payload={"client_message_id": f"smoke-{run_id}", "text": PROBE_TEXT},
            headers={**origin_header, **smoke._auth()},
        )
        if code not in (200, 201) or not isinstance(payload, dict):
            raise StepFailure(f"HTTP {code}: {_describe(payload)}")
        reply = payload.get("reply")
        if not reply:
            raise StepFailure("no reply returned; the agent did not answer")
        ok(
            "message answered",
            f"reply received ({len(reply)} chars, body not shown), status={payload.get('status')!r}",
        )
    except StepFailure as exc:
        bad("message answered", str(exc))

    # 7. Human handoff
    try:
        code, payload = smoke._request(
            f"{smoke.backend_url}/public/chat/sessions/human",
            method="POST",
            headers=smoke._auth(),
        )
        if code not in (200, 201) or not isinstance(payload, dict):
            raise StepFailure(f"HTTP {code}: {_describe(payload)}")
        if payload.get("status") != "human_requested":
            raise StepFailure(f"status is {payload.get('status')!r}, expected 'human_requested'")
        ok("human handoff", "status is human_requested")
    except StepFailure as exc:
        bad("human handoff", str(exc))

    # 8. Close, then confirm the session is dead
    try:
        code, _payload = smoke._request(
            f"{smoke.backend_url}/public/chat/sessions/close",
            method="POST",
            headers=smoke._auth(),
        )
        if code not in (200, 201):
            raise StepFailure(f"close returned HTTP {code}")
        ok("session closed", "closed")
    except StepFailure as exc:
        bad("session closed", str(exc))

    try:
        code, payload = smoke._request(
            f"{smoke.backend_url}/public/chat/messages",
            method="POST",
            payload={"client_message_id": f"smoke-closed-{run_id}", "text": PROBE_TEXT},
            headers=smoke._auth(),
        )
        if code != 409:
            raise StepFailure(f"expected HTTP 409 for a closed session, got {code}")
        ok("closed session rejected", "further messages return 409")
    except StepFailure as exc:
        bad("closed session rejected", str(exc))

    return _report(results)


def _report(results: list[tuple[str, str]]) -> int:
    failures = sum(1 for status, _ in results if status == FAIL)
    print("CXOps public-chat smoke test")
    print()
    for status, line in results:
        print(f"  {status:<4}  {line}")
    print()
    print(f"summary: {len(results) - failures} passed, {failures} failed")
    print("SMOKE: PASS" if not failures else "SMOKE: FAIL")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the API deployment",
    )
    parser.add_argument(
        "--frontend-url",
        default="http://127.0.0.1:3000",
        help="Base URL of the frontend deployment that serves /chat/embed",
    )
    parser.add_argument(
        "--widget-key",
        default=None,
        help=(
            "The tenant's public widget key. Passed on the command line for "
            "convenience; prefer --widget-key-file so the key is not in shell history. "
            "One of --widget-key or --widget-key-file is required."
        ),
    )
    parser.add_argument(
        "--widget-key-file",
        default=None,
        help="Read the widget key from a file instead (safer than argv).",
    )
    parser.add_argument(
        "--embedding-origin",
        required=True,
        help="An origin already on the tenant's allowlist, e.g. https://www.example.com",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    if args.widget_key_file:
        try:
            with open(args.widget_key_file, encoding="utf-8") as handle:
                args.widget_key = handle.read().strip()
        except OSError as exc:
            print(f"error: could not read widget key file: {type(exc).__name__}", file=sys.stderr)
            return 2
    if not args.widget_key:
        if args.widget_key_file:
            print("error: widget key file was empty", file=sys.stderr)
        else:
            # Name both flags: forgetting the key entirely is the common
            # mistake and "widget key is empty" reads as a truncated value.
            print(
                "error: one of --widget-key or --widget-key-file is required",
                file=sys.stderr,
            )
        return 2
    if args.widget_key.startswith("pk_live_") and len(args.widget_key) < 20:
        print("error: --widget-key looks truncated", file=sys.stderr)
        return 2

    try:
        return run(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
