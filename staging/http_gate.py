"""Initial staging HTTP allowlist: fail closed against side effects.

Only authenticated *read-only* staff control-plane operations and liveness
probes are permitted in a hypothetical deployed staging API. This module
does not perform authentication: existing FastAPI dependencies still validate
a Neon-signed identity and a staging-local organization membership.

No externally callable write, webhook, public AI/chat, RAG, integration,
notification, billing or provider route is allowed in initial staging mode.
Future releases must expand this list only after independent review of each
route's transitive side effects and synthetic tenant isolation.
"""
from __future__ import annotations

import os

_INITIAL_STAGING_READ_PATHS = frozenset({
    # Render defaults to probing "/" unless a health path is configured.
    # The existing root handler exposes only public static service metadata.
    "/",
    "/health",
    "/ready",
    "/version",
    "/me/organizations",
    "/me/tenant",
    "/me/authorization",
    # Existing tenant- and capability-gated read routes, synthetic records only.
    "/tickets",
    "/customers",
    "/staff/public-chat/handoff",
    "/staff/public-chat/summary",
})


def staging_request_allowed(method: str, path: str) -> bool:
    """Permit only explicitly named GET/HEAD endpoints, never subpaths.

    Callers must pass the framework-normalized path and uppercase HTTP method.
    Encoded suffixes, method-override headers, and arbitrary routes do not
    create alternate permissions.
    """
    if method == "POST" and path == "/staging/ticket-action":
        # No writes can be sent through the staging middleware unless this
        # separate server-only, staging-branch switch is explicitly enabled.
        return os.environ.get("CXOPS_STAGING_TICKET_ACTIONS_ENABLED") == "true"
    return method in ("GET", "HEAD") and path in _INITIAL_STAGING_READ_PATHS
