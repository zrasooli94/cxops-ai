"""Document CXOps authentication accurately, without changing its runtime guards.

FastAPI's shared dependencies currently extract Authorization via Header/Request
and enforce JWT or visitor-token validation in our own code. They are not
SecurityBase dependencies, so the default OpenAPI incorrectly has no bearer
securitySchemes. This adapter annotates *generated documentation only*: it
does not inject dependency parsers or alter request processing.

Discovery uses the real nested FastAPI dependency graph. Consequently, future
routes using CurrentPrincipal or require_session_token are documented with
the correct, distinct bearer credential without a hardcoded path inventory.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.api.deps import get_current_principal_dep
from app.api.routes.public_chat import require_session_token

STAFF_BEARER_NAME = "StaffJWTBearer"
CHAT_BEARER_NAME = "PublicChatSessionBearer"

SECURITY_SCHEMES: dict[str, dict[str, str]] = {
    STAFF_BEARER_NAME: {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "Signed staff identity JWT. Production verifies JWKS/issuer/audience; "
            "test-only HS256 is not valid in production. Staff authorization "
            "also requires an active organization membership and the route's "
            "server-side capability. X-CXOps-Organization-ID selects a tenant "
            "but never grants access. NOT the opaque visitor chat token."
        ),
    },
    CHAT_BEARER_NAME: {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "Opaque public-chat session bearer token returned by POST "
            "/public/chat/sessions. Grants access only to the matching "
            "visitor conversation; NOT a staff JWT, tenant membership or "
            "business tool authorization."
        ),
    },
}


def _has_dependency(node: Dependant, target: Callable[..., Any]) -> bool:
    """Follow FastAPI's exact dependency graph, including nested RBAC deps."""
    return node.call is target or any(
        _has_dependency(child, target) for child in node.dependencies
    )


def document_authentication(app: FastAPI, spec: dict[str, Any]) -> dict[str, Any]:
    """Apply two auth schemes only to routes that enforce them at runtime.

    This is idempotent under FastAPI's existing OpenAPI schema cache. Machine
    HMAC webhooks and unauthenticated OAuth callback routes are intentionally
    not represented as staff or visitor bearer endpoints; no global security
    requirement is introduced.
    """
    if "security" in spec:
        raise ValueError("CXOps must not acquire a global bearer security requirement")
    components = spec.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    for name, definition in SECURITY_SCHEMES.items():
        current = schemes.setdefault(name, dict(definition))
        if current != definition:
            raise ValueError("OpenAPI bearer scheme conflicts with CXOps contract")

    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        is_staff = _has_dependency(route.dependant, get_current_principal_dep)
        is_chat = _has_dependency(route.dependant, require_session_token)
        if is_staff and is_chat:
            raise ValueError(
                "A route cannot require both staff JWT and visitor session token"
            )
        if not is_staff and not is_chat:
            continue
        scheme = STAFF_BEARER_NAME if is_staff else CHAT_BEARER_NAME
        for method in route.methods or ():
            operation = spec.get("paths", {}).get(route.path_format, {}).get(
                method.lower()
            )
            if operation is None:
                continue
            expected: list[dict[str, list[str]]] = [{scheme: []}]
            existing = operation.get("security")
            if existing is not None and existing != expected:
                raise ValueError("OpenAPI operation conflicts with runtime bearer type")
            operation["security"] = expected
    return spec
