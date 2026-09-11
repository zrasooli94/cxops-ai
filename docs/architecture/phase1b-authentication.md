# Phase 1B — Authentication Architecture

This document describes the authentication boundary established in Phase 1B.
Authorization, tenancy, and multi-agent permissions are deliberately deferred.

---

## Authentication Boundary

Every route that handles sensitive application data requires a valid JWT Bearer
token, enforced through a single FastAPI dependency.

```
Browser / Client
    │  Authorization: Bearer <jwt>
    ▼
┌─ app/api/deps.py ────────────────────────────────┐
│  get_current_principal_dep  →  CurrentPrincipal   │
└────────────────────────┬─────────────────────────┘
                         │
                         ▼
┌─ app/core/auth.py ───────────────────────────────┐
│  get_current_principal                           │
│  • 401 – missing / malformed header              │
│  • 401 – expired token                           │
│  • 401 – invalid signature / claims              │
│  • 500 – dev-mode misconfiguration in production │
│  Returns AuthenticatedPrincipal on success       │
└──────────────────────────────────────────────────┘
```

Routes depend on `CurrentPrincipal` (from `app/api/deps.py`).
No route handler reads or decodes the Authorization header directly.

---

## AuthenticatedPrincipal

`app/core/principal.py` — frozen dataclass, provider-neutral:

| Field        | Description                                          |
|--------------|------------------------------------------------------|
| `subject`    | Stable user ID (`sub` claim), required               |
| `email`      | User email when present, optional                    |
| `issuer`     | `iss` claim, optional                                |
| `auth_method`| How the identity was established (default `"jwt"`)   |
| `token_claims`| Raw claims dict, available for specialized code only |

No organization, role, membership, or tenant data is attached in this phase.

---

## JWT Validation (`app/core/auth.py`)

`jwt.decode` with `python-jose[cryptography]` validates:

1. **Signature** — algorithm matches `AUTH_JWT_ALGORITHM` (default `HS256`)
2. **Expiration** — `exp` claim is always verified
3. **Issuer** — verified only when `AUTH_JWT_ISSUER` is configured
4. **Audience** — verified only when `AUTH_JWT_AUDIENCE` is configured
5. **Subject** — `sub` claim is always required; must be non-empty

Unsupported algorithms (`alg=none`, `RS256` when only `HS256` is allowed)
are rejected by the allowed-algorithm list — no algorithm confusion is possible.

Malformed tokens produce a generic "Invalid token" response. The raw token,
Bearer value, and signing secret never appear in log output or HTTP responses.

---

## Route Classification

### Public (no authentication)

| Route                         | Purpose                                |
|-------------------------------|----------------------------------------|
| `GET /`                       | Service root                           |
| `GET /health`                 | Health probe                           |
| `GET /metrics`                | Prometheus metrics                     |
| `GET /auth/zendesk/login`     | Zendesk OAuth initiation (302/307)     |
| `GET /auth/zendesk/callback`  | Zendesk OAuth callback                 |

### Signed Webhook / System (machine-authenticated, not human JWT)

| Route                            | Protection                                      |
|----------------------------------|-------------------------------------------------|
| `POST /webhooks/ticket-events`   | HMAC-SHA256 (`X-CXOps-Signature`)              |
| `POST /webhooks/zendesk/tickets` | Zendesk HMAC (`x-zendesk-webhook-signature`)   |

Human JWT authentication is never applied to inbound webhook endpoints.
Machine webhooks authenticate with signatures over the raw request body.

#### `POST /webhooks/ticket-events` — machine authentication

This generic inbound webhook ingests ticket events into the `TicketEvent`
log and automation pipeline (shared with the Zendesk webhook path).

- Header: `X-CXOps-Signature`
- Value: lowercase hex encoding of `HMAC-SHA256(secret, raw_request_body)`
- Secret: `TICKET_EVENT_WEBHOOK_SECRET` (environment/config, never hardcoded)
- Verification: constant-time (`hmac.compare_digest`) over the raw body — not
  a re-serialized JSON object — with strict hex-format validation
- Rejections: `401` for missing, malformed, or incorrect signatures
- Fail-closed: an empty/absent secret returns `503` in every environment —
  production never silently accepts unsigned webhooks
- Logging: routes and outcomes only; the secret and raw signature are never
  logged

The Zendesk webhook uses its own protocol (timestamp-prefixed message, base64
digest, `x-zendesk-webhook-signature`) and its own credential, so the two
machines do not share secrets or crypto coupling. This endpoint uses neither
`AUTH_JWT_SECRET`, Zendesk OAuth credentials, nor the Zendesk webhook secret.

### Authenticated (requires valid JWT)

All application data routes — tickets, customers, organizations, knowledge,
observability, agent operations, automation rules, and Zendesk *application*
routes (`/zendesk/*`) — require `CurrentPrincipal`.

---

## Frontend Auth Propagation

`frontend/src/app/api/backend/[...path]/route.ts` forwards the `Authorization`
header from the browser to the backend when present. No server-side secrets
are exposed to JavaScript. The browser-supplied Bearer token is the only
credential forwarded.

A complete frontend login flow (identity provider, token storage, session
management) is not yet implemented and is deferred.

---

## Development Authentication Safety

| Control                              | How                                                         |
|--------------------------------------|-------------------------------------------------------------|
| `AUTH_DEV_MODE` defaults to `False`  | Config field default in `app/core/config.py`                |
| Production startup rejects dev mode  | `Settings` `model_validator` raises `ValueError` if `ENVIRONMENT=production` and `AUTH_DEV_MODE=True` |
| Per-request guard in auth            | `_validate_dev_mode()` → 500 if misconfigured at runtime    |
| No hardcoded production bearer token| Dev secret is `"dev-secret-not-for-production-use"` and only active when `AUTH_DEV_MODE=True` + non-production |

Development mode exists to allow local iteration without a real identity
provider. It must never be enabled in production.

---

## Authentication vs Authorization

**Phase 1B provides authentication only** — verifying the identity of the
caller. Every authenticated request receives a `CurrentPrincipal` but no
decision is made about *what* the caller is allowed to do.

Authorization decisions are deliberately deferred:

- Phase 1C: organization tenancy and multi-tenant data isolation
- Phase 1D: roles, permissions, and the RBAC matrix

---

## Deliberately Deferred to Future Phases

| Item                          | Phase  |
|-------------------------------|--------|
| Organization tenancy          | 1C     |
| Tenant-scoped data filtering  | 1C     |
| Membership                    | 1C/1D  |
| Roles                         | 1D     |
| Permissions                   | 1D     |
| RBAC matrix                   | 1D     |
| Per-org API keys              | 1C     |
| Production OIDC/JWKS provider | Future |
| Frontend login UI             | Future |

### Production OIDC / JWKS (Future)

For production deployment the symmetric HS256 secret must be replaced with
asymmetric key verification via an OIDC provider (e.g. Auth0, Nhost, or a
self-hosted IdP). This requires:

- A JWKS endpoint (`jwks_uri`) for public key discovery
- RS256 or ES256 algorithm support in `AUTH_JWT_ALGORITHM`
- Key rotation awareness (python-jose supports `jwks` key set decoding)
- Trusted issuer configuration via `AUTH_JWT_ISSUER`

The current `app/core/auth.py` architecture is provider-neutral and supports
this transition by changing only the secret-source and algorithm configuration.
