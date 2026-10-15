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

_INITIAL_STAGING_READ_PATHS = frozenset({
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
    return method in ("GET", "HEAD") and path in _INITIAL_STAGING_READ_PATHS
