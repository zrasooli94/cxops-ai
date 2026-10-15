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
from fastapi.routing import APIRoute, iter_route_contexts

from app.api.deps import get_current_principal_dep, verify_ticket_event_signature
from app.api.routes.public_chat import require_session_token
from app.api.routes.zendesk_webhooks import receive_zendesk_ticket_webhook

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



# A webhook signature is NOT an Authorization bearer token. OpenAPI's apiKey
# security type names the signature header; timestamp/invocation requirements
# are separately modeled as mandatory request headers below. One header alone
# never constitutes a valid authenticated webhook.
GENERIC_HMAC_NAME = "TicketEventHMAC"
ZENDESK_HMAC_NAME = "ZendeskWebhookHMAC"

MACHINE_SECURITY_SCHEMES: dict[str, dict[str, str]] = {
    GENERIC_HMAC_NAME: {
        "type": "apiKey",
        "in": "header",
        "name": "X-CXOps-Signature",
        "description": (
            "64 hexadecimal HMAC-SHA256 signature over UTF-8 "
            "X-CXOps-Timestamp EXACTLY AS SENT, concatenated with the raw "
            "HTTP body bytes (no delimiter). X-CXOps-Timestamp is mandatory "
            "and subject to GENERIC_WEBHOOK_REPLAY_WINDOW_SECONDS. The "
            "server-side secret is never a client-visible API key. No staff "
            "JWT or public-chat bearer is accepted as machine authentication. "
            "Time-window validation is NOT single-use replay prevention."
        ),
    },
    ZENDESK_HMAC_NAME: {
        "type": "apiKey",
        "in": "header",
        "name": "X-Zendesk-Webhook-Signature",
        "description": (
            "Base64 HMAC-SHA256 of the exact UTF-8 "
            "X-Zendesk-Webhook-Signature-Timestamp string followed immediately "
            "by raw HTTP body bytes. A webhook secret is resolved ONLY from "
            "the organization-owned connection identified by integration_id. "
            "Timestamp and X-Zendesk-Webhook-Invocation-Id are mandatory. "
            "Invocation ID is NOT included in the HMAC; it is a separate "
            "idempotency key, not a cryptographic single-use nonce. "
            "No staff JWT or visitor bearer token grants access."
        ),
    },
}

MACHINE_HEADERS: dict[str, tuple[tuple[str, str], ...]] = {
    GENERIC_HMAC_NAME: (
        (
            "X-CXOps-Timestamp",
            (
                "Timestamp as sent by the producer: signed with the raw body; "
                "ISO-8601 or positive Unix epoch, within configured replay window."
            ),
        ),
    ),
    ZENDESK_HMAC_NAME: (
        (
            "X-Zendesk-Webhook-Signature-Timestamp",
            (
                "Timestamp as sent by Zendesk: signed with raw body; must be within "
                "the configured Zendesk replay window."
            ),
        ),
        (
            "X-Zendesk-Webhook-Invocation-Id",
            (
                "Required delivery/idempotency identifier. This header is NOT "
                "part of the Zendesk HMAC and cannot prove a one-time request."
            ),
        ),
    ),
}

MACHINE_METADATA: dict[str, dict[str, str]] = {
    GENERIC_HMAC_NAME: {
        "x-cxops-hmac-input": "utf8(exact-timestamp-header) + raw-body-bytes",
        "x-cxops-hmac-output": "sha256-hex",
        "x-cxops-replay-window-config": "GENERIC_WEBHOOK_REPLAY_WINDOW_SECONDS",
        "x-cxops-deduplication-key": "signed-payload.event_id",
    },
    ZENDESK_HMAC_NAME: {
        "x-cxops-hmac-input": "utf8(exact-timestamp-header) + raw-body-bytes",
        "x-cxops-hmac-output": "sha256-base64",
        "x-cxops-replay-window-config": "ZENDESK_WEBHOOK_REPLAY_WINDOW_SECONDS",
        "x-cxops-deduplication-key": "unsigned-header.X-Zendesk-Webhook-Invocation-Id",
    },
}

MACHINE_RESPONSES: dict[str, dict[str, str]] = {
    GENERIC_HMAC_NAME: {
        "401": "Missing or invalid HMAC signature / timestamp",
        "422": "Invalid signed JSON ticket-event payload",
        "503": "Server-side ticket-event signing secret not configured",
    },
    ZENDESK_HMAC_NAME: {
        "400": "Missing Zendesk webhook invocation ID",
        "401": "Missing or invalid signature, timestamp or integration secret",
        "404": "Integration ID not found",
        "422": "Malformed Zendesk event JSON or missing ticket fields",
    },
}


def _document_machine_headers(operation: dict[str, Any], scheme: str) -> None:
    """Document required non-secret headers separately from the signature key.

    Reject incompatible existing definitions instead of silently rewriting
    an OpenAPI contract already installed by another dependency or router.
    """
    parameters = operation.setdefault("parameters", [])
    for name, description in MACHINE_HEADERS[scheme]:
        expected = {
            "name": name,
            "in": "header",
            "required": True,
            "schema": {"type": "string", "minLength": 1},
            "description": description,
        }
        matching = [
            parameter
            for parameter in parameters
            if parameter.get("in") == "header"
            and parameter.get("name", "").lower() == name.lower()
        ]
        if matching:
            if len(matching) != 1 or matching[0] != expected:
                raise ValueError("OpenAPI webhook header conflicts with machine contract")
        else:
            parameters.append(expected)

    for key, value in MACHINE_METADATA[scheme].items():
        current = operation.setdefault(key, value)
        if current != value:
            raise ValueError("OpenAPI webhook metadata conflicts with signed-message contract")

    responses = operation.setdefault("responses", {})
    for code, description in MACHINE_RESPONSES[scheme].items():
        responses.setdefault(code, {"description": description})


def _has_dependency(node: Dependant, target: Callable[..., Any]) -> bool:
    """Follow FastAPI's exact dependency graph, including nested RBAC deps."""
    return node.call is target or any(
        _has_dependency(child, target) for child in node.dependencies
    )


def document_authentication(app: FastAPI, spec: dict[str, Any]) -> dict[str, Any]:
    """Apply the actual auth type only to routes that enforce it at runtime.

    This is idempotent under FastAPI's existing OpenAPI schema cache.
    Signed machine webhooks use distinct header-based HMAC contracts; OAuth
    callbacks are deliberately not labeled with staff or visitor bearers.
    No global security requirement is introduced.
    """
    if "security" in spec:
        raise ValueError("CXOps must not acquire a global bearer security requirement")
    components = spec.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    for name, definition in {**SECURITY_SCHEMES, **MACHINE_SECURITY_SCHEMES}.items():
        current = schemes.setdefault(name, dict(definition))
        if current != definition:
            raise ValueError("OpenAPI bearer scheme conflicts with CXOps contract")

    # FastAPI 0.141 uses _IncludedRouter to keep nested routers lazy.
    # This is the same flattening API used by FastAPI's own get_openapi():
    # inspecting app.routes directly sees only the wrapper, not its endpoints.
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.include_in_schema:
            continue
        is_staff = _has_dependency(route.dependant, get_current_principal_dep)
        is_chat = _has_dependency(route.dependant, require_session_token)
        is_generic = _has_dependency(
            route.dependant, verify_ticket_event_signature
        )
        # Zendesk authenticates in the actual route handler rather than a
        # FastAPI dependency. Bind to the real callable, never a matching URL
        # substring that would incorrectly label another public route.
        is_zendesk = route.dependant.call is receive_zendesk_ticket_webhook
        if sum((is_staff, is_chat, is_generic, is_zendesk)) > 1:
            raise ValueError("One route cannot mix staff, visitor and machine authentication")
        if not any((is_staff, is_chat, is_generic, is_zendesk)):
            continue
        if is_staff:
            scheme = STAFF_BEARER_NAME
        elif is_chat:
            scheme = CHAT_BEARER_NAME
        elif is_generic:
            scheme = GENERIC_HMAC_NAME
        else:
            scheme = ZENDESK_HMAC_NAME
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
            if scheme in MACHINE_SECURITY_SCHEMES:
                _document_machine_headers(operation, scheme)
    return spec
