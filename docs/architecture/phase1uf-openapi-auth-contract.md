# Phase 1U-F — OpenAPI authentication and authorization contracts

**Status:** draft; docs-only implementation with no authentication enforcement
change, no migrations, provider calls, changes to production configuration or
new dependencies.

## Problem

The production JWT authenticator extracts the `Authorization` header using
FastAPI's `Header` dependency and authenticates through the existing
`get_current_principal_dep` / `get_current_principal` functions.
Public web-chat session operations independently parse an opaque visitor
Bearer token in `require_session_token`. Neither uses FastAPI's
`SecurityBase` dependency, so the generated OpenAPI lacks security schemes
and incorrectly suggests protected staff APIs require no credentials.

The visitor token is **not** the staff JWT. A generic global bearer scheme
would therefore be misleading. Business-tool authorization, membership and
role capabilities remain enforced by the server, not JWT scopes.

## Chosen design

Add a small OpenAPI-only post-processor, not a second runtime HTTPBearer
dependency. It discovers authentication guards through each route's actual
nested FastAPI dependency graph, then assigns either:

- `StaffJWTBearer`: JWT with production JWKS verification, tenant-scoped
  membership, and server-side capabilities.
- `PublicChatSessionBearer`: opaque visitor session token returned only by
  `POST /public/chat/sessions`, without staff or tool privileges.

No global OpenAPI security default is set. The six staff handoff APIs have
`x-cxops-required-capability` metadata: `ticket.read` for listing,
summary and business-action reads, and `ticket.write` for assigning,
releasing or resolving. Response docs advertise 401 invalid credentials, 403
membership/capability denial, and 409 ambiguous tenant selection/conflict.
These are **vendor extensions / documentation**, not OAuth scopes.

The app includes nested FastAPI routers, which in pinned FastAPI 0.141 are
represented as lazy `_IncludedRouter` objects at the top level. The adapter
uses FastAPI's own `routing.iter_route_contexts(app.routes)` flattening
function (also used by `get_openapi`) to resolve effective full paths and
dependency graphs. Using `app.routes` directly missed the nested JWT and
visitor-token routes; a prior CI run exposed and prevented that unsafe
documentation result. A future FastAPI upgrade must rerun the complete
OpenAPI route-inventory tests before release.

## Security invariants

- The JWT parser and signature verification, claim validation, error mapping,
  current tenant resolution and capability guard code are unchanged.
- The visitor session-token parser, widget-origin restrictions and session
  authorization remain exactly as before.
- Machine HMAC webhooks and public OAuth callback routes are **not** falsely
  described as staff JWT or visitor Bearer endpoints. HMAC details are a
  separate machine-auth contract milestone.
- No second Authorization parser is invoked during requests. The OpenAPI
  post-processor runs only when the API schema is requested; FastAPI's
  cached schema remains idempotent.
- Tests prove all current routes inherited the correct security scheme from
  their existing dependency graph, explicitly check the public/private route
  matrix, and protect staff capability documentation and existing session ID
  constraints.
- Nothing enables notifications, lead capture, or live model evaluations.

## CI and release

Run Ruff, mypy, backend tests (disposable PostgreSQL), public/staff Schemathesis
against disposable PostgreSQL, Docker, frontend, and secret scanning. No new
paid infrastructure, live JWT/IdP credentials or model-provider keys are
needed. Review and merge only after all checks pass on the final commit.
