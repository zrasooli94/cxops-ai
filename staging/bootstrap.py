"""Safe, no-dependencies bootstrap for an isolated FREE Render staging service.

This module deliberately never imports app.main, config, any integration,
database client, or external provider. It does NOT implement authentication,
ticket APIs, webhook endpoints or production-compatible health readiness.

Production deployment uses its existing start command and is unchanged.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class StagingBootstrapHandler(BaseHTTPRequestHandler):
    """Read-only public holding page until staging identity is provisioned."""

    server_version = "CXOps-Staging-Bootstrap"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: object) -> None:
        # Do not log URL paths, queries, Authorization or sensitive request data.
        return

    def _respond(self, status: int, payload: dict[str, object], *, send_body: bool = True) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/", "/health", "/status"):
            self._respond(200, {
                "service": "cxops-ai-staging-bootstrap",
                "state": "identity-and-backend-not-enabled",
                "live_customer_data": False,
                "real_staff_actions": False,
            })
        elif path == "/ready":
            self._respond(503, {"ready": False, "reason": "staging-auth-not-provisioned"})
        else:
            self._respond(503, {"detail": "Staging application is not enabled."})

    def do_HEAD(self) -> None:
        # Remain unready for business traffic while allowing container
        # visibility/status probes. Never issue request forwarding.
        path = urlsplit(self.path).path
        if path in ("/", "/health", "/status"):
            self._respond(200, {"state": "bootstrap-only"}, send_body=False)
        else:
            self._respond(503, {"detail": "Staging application is not enabled."}, send_body=False)

    def do_POST(self) -> None:
        self._respond(503, {"detail": "Staging writes are disabled."})

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def main() -> None:
    # A safe Render-provided PORT is required; there is no default localhost
    # database, no outbound network and no access to production secrets.
    port = int(os.environ["PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be in the TCP port range")
    server = ThreadingHTTPServer(("0.0.0.0", port), StagingBootstrapHandler)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
