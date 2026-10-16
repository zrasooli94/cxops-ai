"""No database, tokens or network providers: local loopback-only HTTP tests."""
from __future__ import annotations

import http.client
import json
from http.server import ThreadingHTTPServer
from threading import Thread

from staging.bootstrap import StagingBootstrapHandler


def _request(method: str, path: str, body: str | None = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), StagingBootstrapHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request(method, path, body=body)
        response = connection.getresponse()
        status = response.status
        headers = dict(response.getheaders())
        data = response.read()
        connection.close()
        return status, headers, data
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_bootstrap_status_has_no_customer_content():
    status, headers, body = _request("GET", "/")
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body) == {
        "service": "cxops-ai-staging-bootstrap",
        "state": "identity-and-backend-not-enabled",
        "live_customer_data": False,
        "real_staff_actions": False,
    }


def test_staging_is_not_marked_ready_for_actual_work():
    status, _, body = _request("GET", "/ready")
    assert status == 503
    assert json.loads(body)["ready"] is False


def test_all_operational_and_webhook_actions_refuse_requests():
    for method, path in [
        ("GET", "/staff/public-chat/handoff"),
        ("POST", "/webhooks/zendesk/tickets/123"),
        ("POST", "/public/leads"),
        ("PUT", "/api/customer/12"),
        ("PATCH", "/api/customer/12"),
        ("DELETE", "/api/customer/12"),
    ]:
        status, _, body = _request(method, path, '{"synthetic":"only"}')
        assert status == 503
        assert b"disabled" in body or b"not enabled" in body


def test_head_probes_are_bodyless():
    status, _, body = _request("HEAD", "/health")
    assert status == 200
    assert not body
