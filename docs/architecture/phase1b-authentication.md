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

---

## Frontend Server-Side Session Foundation (Phase 1B.2b1)

This phase establishes the reusable session infrastructure. Login UI, sign-in
actions, and logout are deferred to Phase 1B.2b2.

### Nhost Provider Configuration (verified real values)

| Key | Value |
|-----|-------|
| Subdomain | `qghtvniyltmrsaoxruwa` |
| Region | `ap-southeast-1` |
| Issuer | `https://qghtvniyltmrsaoxruwa.auth.ap-southeast-1.nhost.run/v1` |
| Audience | Not present in current real access tokens |
| JWKS | `https://qghtvniyltmrsaoxruwa.auth.ap-southeast-1.nhost.run/v1/.well-known/jwks.json` |

Frontend environment variables (`.env.example`):

```
NEXT_PUBLIC_NHOST_SUBDOMAIN=qghtvniyltmrsaoxruwa
NEXT_PUBLIC_NHOST_REGION=ap-southeast-1
NHOST_SESSION_COOKIE=nhostSession
NODE_ENV=development
```

### Session Architecture

**Intended flow (to be completed in 1B.2b2/3):**

```
Browser
  │  (credentials → Nhost Auth)
  ▼
Next.js server (server actions / route handlers)
  │  createServerClient() with cookie SessionStorageBackend
  │  signInEmailPassword() → session stored in Secure, httpOnly cookie
  ▼
server-managed session (cookie: nhostSession)
  │  cookie auto-sent on same-site requests
  ▼
Next.js backend proxy route
  │  reads session cookie → accessToken
  │  forwards Authorization: Bearer <accessToken>
  ▼
FastAPI JWKS verification → AuthenticatedPrincipal
```

### Server Client Helper

`frontend/src/lib/nhost/server.ts` exports `createNhostServerClient()`:

- Creates a Nhost server client via `createServerClient()` from `@nhost/nhost-js`
- Uses configured `NEXT_PUBLIC_NHOST_SUBDOMAIN` / `NEXT_PUBLIC_NHOST_REGION`
- Implements `SessionStorageBackend` backed by Next.js `cookies()`:
  - `get()` → reads `nhostSession` cookie, safe JSON parse, returns `StoredSession | null`
  - `set()` → writes cookie with `Secure` (production), `httpOnly: true`, `sameSite: "lax"`, `path: "/"`, 30-day `maxAge`
  - `remove()` → deletes cookie
- Never uses Admin Secret
- No global mutable session state (new client per call)

### Session Serialization Safety

`frontend/src/lib/session/helpers.ts` provides pure, testable helpers:

- `serializeSession(session)` → JSON string
- `deserializeSession(raw)` → `StoredSession | null` (validates required fields, fails safely on malformed JSON)
- `getAccessToken(session)` → `string | null`
- `hasValidSession(session)` → `boolean`

Unit tests (`helpers.test.ts`) cover valid/invalid deserialization, missing fields, null handling, access-token extraction, and session validity checks. Run with `node --experimental-strip-types --test src/lib/session/helpers.test.ts`.

### Cookie Security Design

| Property | Value | Rationale |
|----------|-------|-----------|
| Name | `nhostSession` (configurable via `NHOST_SESSION_COOKIE`) | Predictable |
| Secure | `true` in production (`NODE_ENV=production`) | HTTPS only |
| httpOnly | `true` | Browser JS cannot read refresh token |
| sameSite | `lax` | Allows same-site navigation; CSRF-safe for GET |
| path | `/` | Available on all routes |
| maxAge | 30 days (2,592,000s) | Matches Nhost default refresh token TTL |
| Domain | Not set (defaults to current host) | Works on subdomains |

**Trade-off note:** The Nhost SDK's own `CookieStorage` class defaults to `httpOnly: false` to allow client-side access. Our custom backend deliberately uses `httpOnly: true` because the session cookie is managed server-side and the CXOps application JavaScript does not need direct access to the refresh token. The access token is only used server-side when forwarding to the backend proxy.

### Backend Proxy Plan (Phase 1B.2b3)

Current `frontend/src/app/api/backend/[...path]/route.ts` forwards an incoming `Authorization` header if present. The Phase 1B.2b3 change will:

1. In the proxy route, call `createNhostServerClient()` to get the server client
2. Call `nhost.getUserSession()` to read the session from the cookie
3. Extract `accessToken` via `getAccessToken()`
4. Set `Authorization: Bearer <accessToken>` on the outgoing request to FastAPI
5. Preserve existing behavior: if an incoming `Authorization` header exists (dev/test), it is still forwarded

This keeps the backend proxy stateless and the FastAPI JWKS verification unchanged.

### Session Logout Helper

`frontend/src/lib/session/logout.ts` exports a server action `logout()` that returns a `LogoutResult`:

```ts
interface LogoutResult {
  localCleared: boolean;
  remoteRevoked: boolean;
  error?: string;
}
```

Behavior:

1. Creates a server client via `createNhostServerClient()`
2. Reads the current session via `nhost.getUserSession()`
3. If a session with `refreshToken` exists, calls `nhost.auth.signOut({ refreshToken: session.refreshToken })` to revoke the server-side refresh token
4. Calls `nhost.clearSession()` to remove the local session state
5. Deletes the `nhostSession` cookie from the response
6. Returns `{ localCleared: true, remoteRevoked: true }` on full success

Failure semantics (security-first local logout):

- **Local logout is guaranteed**: the session cookie is always deleted before returning to the browser, even if remote revocation fails
- **Remote revocation is best-effort**: Nhost `signOut` is attempted first; if it fails (network error, non-2xx, non-"OK" body), the local cookie is still cleared
- **Result indicates partial success**: `{ localCleared: true, remoteRevoked: false, error: "Remote session revocation could not be confirmed" }`
- **No automatic retry**: because the stateless local session is erased, the refresh token is no longer available to retry remote revocation
- **No token exposure**: errors never contain the refresh token, access token, or provider response body
- **No Admin Secret used**

Idempotency:

- If no session exists, `logout()` is a no-op that still ensures the cookie is deleted
- If a session exists but lacks a `refreshToken`, local state is cleared and the error indicates remote revocation was not performed (not that it failed)

The pure logic is in `frontend/src/lib/session/logout-helpers.ts` (`performLogout`) and is covered by 8 deterministic unit tests in `logout.test.ts`.

### Server-Side Session Refresh Requirement

`createServerClient()` intentionally **disables automatic session refresh** in server contexts (per Nhost SDK design) to prevent race conditions in concurrent server requests.

Phase 1B.2b2/1B.2b3 **must** deliberately perform proactive session refresh near token expiry. The intended flow at the Next.js request/proxy boundary:

```
incoming request
  │  read session cookie via createNhostServerClient()
  ▼
if access token expires soon (e.g., < 60s)
  │  nhost.refreshSession(60)
  │  → updated session written to response cookies via SessionStorageBackend.set()
  ▼
use current (possibly refreshed) accessToken
  │  set Authorization: Bearer <accessToken>
  ▼
forward to FastAPI backend
```

This ensures the cookie carries a valid access token for the proxied request, and the refreshed session is available for subsequent requests.

### What This Phase Does NOT Implement

- Login page / UI
- Sign-in server action (`signInEmailPassword()`)
- Logout UI/button
- Protected navigation / Control Center gating
- Organization tenancy or RBAC

These are Phase 1B.2b2 (login/logout UI and protected routes) and Phase 1B.2b3 (proxy token injection).

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

### Production OIDC / JWKS (Phase 1B.2)

For production deployment the symmetric HS256 secret is replaced with
asymmetric key verification against a JWKS endpoint. The implemented
`app/core/jwks.py` is a generic, provider-neutral adapter compatible with
any OIDC provider publishing a standard JWKS document (Nhost with
asymmetric signing, Auth0, Keycloak, etc.).

Configuration (see `.env.example`, values below are required and must come
from the deployed provider's real configuration — no project-specific
values are asserted in this repository):
```
AUTH_MODE=jwks
AUTH_JWKS_URL=<deployed project's documented JWKS endpoint>
AUTH_JWKS_ALGORITHMS=RS256
AUTH_JWT_ISSUER=<actual iss claim of the deployed project, if enforced>
AUTH_JWT_AUDIENCE=<actual aud claim of the deployed project, if enforced>
AUTH_JWKS_CACHE_TTL_SECONDS=600
```

Nhost deployment notes — how to configure the verifier without guessing:
- Nhost currently defaults to **symmetric** (HS256) JWT signing.
  Asymmetric (RS256) signing **must be explicitly enabled** in the Nhost
  project settings before a JWKS endpoint is available.
- `AUTH_JWKS_URL` must point at the deployed Nhost project's documented
  JWKS endpoint. Do not hardcode a guessed subdomain/region: confirm it
  against the deployed Nhost Auth configuration.
- `AUTH_JWT_ISSUER`, when enforced, must match the actual `iss` claim in
  tokens the deployed project issues. Read the claim from a safely decoded
  development token and confirm it against the deployed Nhost issuer value
  before pinning it — do not assume it equals the base URL.
- `AUTH_JWT_AUDIENCE`, when enforced, must match the actual `aud` claim in
  the issued tokens / the project's configured client identifier. Inspect
  the tokens the project actually issues rather than assuming the client ID
  is the audience.
- The CXOps verifier validates issuer and audience only when configured
  (empty = optional).
- JWKS keys are cached per `kid` for the configured TTL. Rotation is
  discovered on the next fetch after a cache miss. A key removed from the
  JWKS stops being honored once its cached entry falls out of TTL. There is
  **no** stale-key grace period beyond the normal TTL: if the JWKS endpoint
  is unreachable and no fresh cached key exists for the presented `kid`,
  the request fails closed with `503` instead of trusting possibly-revoked
  keys. This fail-closed trade-off is deliberate — availability is
  sacrificed to guarantee a rotated-out key is never accepted.

Do not hardcode a real project subdomain, region, signing key, or secret
in this repository. When the live project is wired in a later phase,
inspect a safely decoded development token and the deployed Nhost
configuration before pinning issuer/audience values.
