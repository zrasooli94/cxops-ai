"""Phase 1U-F: OpenAPI JWT, visitor tokens and capability contracts.

Documentation-only test: no database, network calls, authentication override,
mocked authorization decision, or production secrets.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from app.api.deps import get_current_principal_dep
from app.api.routes.public_chat import require_session_token
from app.core.openapi_security import (
    CHAT_BEARER_NAME,
    SECURITY_SCHEMES,
    STAFF_BEARER_NAME,
    _has_dependency,
)
from app.main import app


def _operation(path: str, method: str) -> dict:
    return app.openapi()["paths"][path][method.lower()]


def test_openapi_has_distinct_bearer_schemes_and_no_global_staff_default():
    spec = app.openapi()
    assert "security" not in spec
    defined = spec["components"]["securitySchemes"]
    for name, expected in SECURITY_SCHEMES.items():
        assert defined[name] == expected
        assert expected["type"] == "http"
        assert expected["scheme"] == "bearer"
    assert defined[STAFF_BEARER_NAME]["bearerFormat"] == "JWT"
    assert "bearerFormat" not in defined[CHAT_BEARER_NAME]
    assert "membership" in defined[STAFF_BEARER_NAME]["description"]
    assert "opaque" in defined[CHAT_BEARER_NAME]["description"].lower()


def test_staff_reads_and_writes_require_jwt_and_correct_capability():
    routes = (
        ("get", "/staff/public-chat/summary", "ticket.read"),
        ("get", "/staff/public-chat/handoff", "ticket.read"),
        (
            "get", "/staff/public-chat/sessions/{session_id}/business-actions",
            "ticket.read",
        ),
        ("post", "/staff/public-chat/sessions/{session_id}/assign", "ticket.write"),
        ("post", "/staff/public-chat/sessions/{session_id}/release", "ticket.write"),
        ("post", "/staff/public-chat/sessions/{session_id}/resolve", "ticket.write"),
    )
    for method, path, capability in routes:
        operation = _operation(path, method)
        assert operation["security"] == [{STAFF_BEARER_NAME: []}]
        assert operation["x-cxops-required-capability"] == capability
        for code in ("401", "403", "409"):
            assert code in operation["responses"]
        if "{session_id}" in path:
            parameter = next(
                p for p in operation["parameters"] if p["name"] == "session_id"
            )
            assert parameter["schema"]["minimum"] == 1
            assert parameter["schema"]["maximum"] == 2_147_483_647


def test_widget_credentials_are_never_documented_as_staff_jwt():
    visitor = (
        ("post", "/public/chat/messages"),
        ("get", "/public/chat/sessions/state"),
        ("get", "/public/chat/sessions/updates"),
        ("post", "/public/chat/sessions/human"),
        ("post", "/public/chat/sessions/close"),
    )
    for method, path in visitor:
        assert _operation(path, method)["security"] == [{CHAT_BEARER_NAME: []}]
    for method, path in (
        ("get", "/public/chat/config"),
        ("post", "/public/chat/sessions"),
        ("get", "/public/leads/availability"),
        ("post", "/public/leads"),
        ("get", "/health"),
        ("get", "/ready"),
        ("get", "/version"),
        ("get", "/auth/zendesk/callback"),
    ):
        assert not _operation(path, method).get("security"), (method, path)


def test_other_staff_and_machine_auth_are_never_conflated():
    # This is still the real CurrentPrincipal tree, not a list of JWT routes.
    for method, path in (
        ("get", "/tickets"),
        ("get", "/me/organizations"),
        ("get", "/auth/zendesk/login"),
        ("post", "/invitations/accept"),
    ):
        assert _operation(path, method)["security"] == [{STAFF_BEARER_NAME: []}]
    for path in ("/webhooks/ticket-events", "/webhooks/zendesk/tickets/{integration_id}"):
        operation = app.openapi()["paths"].get(path, {}).get("post", {})
        assert {STAFF_BEARER_NAME: []} not in operation.get("security", [])
        assert {CHAT_BEARER_NAME: []} not in operation.get("security", [])


def test_documentation_matches_every_route_current_dependency_graph():
    spec = app.openapi()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        needs_staff = _has_dependency(route.dependant, get_current_principal_dep)
        needs_chat = _has_dependency(route.dependant, require_session_token)
        assert not (needs_staff and needs_chat)
        expected = (
            [{STAFF_BEARER_NAME: []}]
            if needs_staff
            else [{CHAT_BEARER_NAME: []}] if needs_chat else None
        )
        for method in route.methods or ():
            operation = spec.get("paths", {}).get(route.path_format, {}).get(
                method.lower()
            )
            if operation is None:
                continue
            if expected is None:
                assert not operation.get("security"), (method, route.path)
            else:
                assert operation.get("security") == expected, (
                    method, route.path
                )


def test_schema_cache_is_idempotent_and_never_leaks_credentials():
    first = app.openapi()
    second = app.openapi()
    assert first is second
    assert first["components"]["securitySchemes"] == second["components"]["securitySchemes"]
    assert "Authorization: Bearer " not in str(first)
    assert "dev-secret-not-for-production-use" not in str(first)


def test_runtime_jwt_dependency_remains_header_based():
    # The metadata adapter may not insert SecurityBase or HTTPBearer into the
    # runtime dependency tree: existing JWT validation owns all 401/403 checks.
    auth_routes = [r for r in app.routes if isinstance(r, APIRoute)]
    def calls(node):
        name = getattr(node.call, "__qualname__", str(type(node.call)))
        module = getattr(node.call, "__module__", "unknown")
        return [(module, name)] + [
            item for dep in node.dependencies for item in calls(dep)
        ]

    assert auth_routes, [
        (type(r).__module__, type(r).__name__, getattr(r, "path", None))
        for r in app.routes
    ]
    staff_candidates = [
        (type(r).__name__, getattr(r, "path_format", None), getattr(r, "path", None))
        for r in app.routes
        if "staff" in str(getattr(r, "path", ""))
    ]
    visitor_candidates = [
        (type(r).__name__, getattr(r, "path_format", None), getattr(r, "path", None))
        for r in app.routes
        if "public/chat/messages" in str(getattr(r, "path", ""))
    ]
    assert any(r.path_format == "/staff/public-chat/handoff" for r in auth_routes), (
        staff_candidates
    )
    assert any(r.path_format == "/public/chat/messages" for r in auth_routes), (
        visitor_candidates
    )
    staff = next(
        r for r in auth_routes if r.path_format == "/staff/public-chat/handoff"
    )
    visitor = next(
        r for r in auth_routes if r.path_format == "/public/chat/messages"
    )
    assert _has_dependency(staff.dependant, get_current_principal_dep), calls(staff.dependant)
    assert _has_dependency(visitor.dependant, require_session_token), calls(visitor.dependant)
    assert get_current_principal_dep.__name__ == "get_current_principal_dep"
    assert require_session_token.__name__ == "require_session_token"
