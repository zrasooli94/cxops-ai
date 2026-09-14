# Phase 1C.1 — Organization Membership & Trusted Tenant Context Foundation

This document describes the tenant foundation established in Phase 1C.1: the
durable link between authenticated external identities and CXOps organizations.

RBAC, admin/supervisor/agent permissions, the organization switcher UI, and
tenant-scoping every existing repository are **deliberately deferred**.

- RBAC → Phase 1D
- UI organization switching → Phase 1C.4
- Tenant-scoping every repository → Phase 1C.2+

---

## Identity ≠ Tenant

The single most important invariant of this phase:

| Question | Answers                  | Produces                  |
|----------|--------------------------|---------------------------|
| "Who is this user?"          | Authentication (Phase 1B)  | `AuthenticatedPrincipal`   |
| "Which organization may this authenticated user operate inside?" | Tenant resolution (Phase 1C.1) | `TenantContext` |

Authentication proves identity. Tenancy proves which organization a verified
identity is allowed to operate inside. These are resolved in sequence and are
never conflated: a valid JWT grants **identity**, not **tenancy**.

---

## Resolution Flow

```
Nhost Auth
  → RS256 JWT (sub = stable external user id)
  → Next.js BFF
  → FastAPI JWKS verification        (Phase 1B)
  → AuthenticatedPrincipal           (app/core/principal.py)
  → organization_memberships lookup  (bound to subject)
  → TenantContext                    (app/core/tenant.py)
  → future tenant-scoped repositories (Phase 1C.2+)
```

`TenantContext` is small and immutable (frozen dataclass):

```python
@dataclass(frozen=True)
class TenantContext:
    organization_id: int
    subject: str
```

It carries no roles and no raw claims. Authorization roles arrive in Phase 1D.

---

## organization_memberships

`app/models/organization_membership.py` — durable identity↔organization link.

| Column          | Type                | Constraint                            |
|-----------------|---------------------|---------------------------------------|
| `id`            | integer PK auto      |                                       |
| `organization_id` | integer FK → organizations.id | NOT NULL                    |
| `subject`       | varchar(255)         | NOT NULL                              |
| `created_at`    | timestamptz          | server default now()                  |

Constraints:

- `UNIQUE (organization_id, subject)` — one membership per identity per org
- `ix_organization_memberships_subject` — subject lookup index

Security-critical rules for this table:

- `subject` is the Nhost JWT `sub` — the external, stable user identity.
- **Never** store access tokens, refresh tokens, JWTs, or cookies here.
- Membership rows are just grants — no role/status columns. Business roles
  (admin, supervisor, agent) belong to Phase 1D.

---

## Tenant Resolution Strategy (Phase 1C.1)

`app/services/tenant_service.py` → `resolve_tenant_context(db, principal, requested_organization_id=None)`

| Memberships | Selector supplied | Result                                         |
|-------------|-------------------|------------------------------------------------|
| 0           | any               | deny → **403** (`TenantMembershipMissingError`) |
| 1           | none              | auto-resolve → **200**                          |
| 1           | matching          | validate + resolve → **200**                    |
| 1           | non-matching      | deny → **403** (`TenantAccessDeniedError`)      |
| 2+          | none              | deny → **409** (`TenantMembershipAmbiguousError`) |
| 2+          | matching          | validate + resolve → **200**                    |
| 2+          | non-matching      | deny → **403** (`TenantAccessDeniedError`)      |

Multiple-membership behavior is deterministic by design:

- An explicit selector is **required** when > 1 membership exists; absent
  selector is a **409 Conflict**, never a random or first-row pick.
- List ordering uses `ORDER BY organization_id` so behavior is fully
  reproducible.
- If a selector is present it is always validated against
  `(subject, organization_id)` before resolution.

---

## Tenant Selector Security

`X-CXOps-Organization-ID` — if present — is a **selector, and only a
selector. It is never proof of authorization.**

```
requested_organization_id  +  authenticated principal.subject
        →  membership DB validation (subject AND organization_id bound)
        →  TenantContext
```

A user can never gain tenant access by changing a header or query parameter:

- The lookup is `WHERE subject = :subject AND organization_id = :organization_id`
  — never `SELECT organization WHERE id = client_id`.
- Organization id is **never** taken from the JWT claims; it comes only from
  memberships resolved against the authenticated subject.
- Malformed/invalid selectors fail closed with **403**.

This is enforced centrally in `app/api/deps.py::get_current_tenant`, so every
endpoint that depends on `CurrentTenant` inherits the same rule.

### BFF transport (Next.js)

`frontend/src/app/api/backend/[...path]/route.ts` forwards the selector to
FastAPI through `selectForwardHeaders` (`frontend/src/lib/auth/proxy-headers.ts`).
The forwarded-header allowlist is:

`content-type`, `accept`, `x-request-id`, `x-cxops-organization-id`

The BFF only **transports** the selector — it never interprets or authorizes
the value. Cookies and `Host` remain blocked. Authorization is exclusively
FastAPI → `CurrentPrincipal` → `CurrentTenant` → DB membership validation; a
header is not trusted merely because it arrived through the authenticated BFF.
The organization id is never added to a JWT/session as an authorization
shortcut.

---

## FastAPI Dependency

```python
async def get_my_tenant(
    principal: CurrentPrincipal,   # who (Phase 1B)
    tenant: CurrentTenant,         # which org (Phase 1C.1)
    ...
)
```

`CurrentTenant` (`app/api/deps.py`) depends on `CurrentPrincipal`, opens a DB
session, applies the resolution strategy above, and maps errors:

| Error                          | HTTP           |
|--------------------------------|----------------|
| `TenantMembershipMissingError` | 403            |
| `TenantMembershipAmbiguousError` | 409          |
| `TenantAccessDeniedError`      | 403            |

Only one proof endpoint is wired in this phase to keep the blast radius tiny:
`GET /me/tenant` (returns `{organization_id, organization_name}`). No other
routes are tenant-scoped yet.

---

## Observability

Structured events:

- `tenant_resolution_success` — subject + organization_id + request_id
- `tenant_membership_missing` — subject + request_id
- `tenant_access_denied` — subject + organization_id + request_id
- `tenant_invalid_selector` — subject + header name (never the raw value)

Never logged: JWT, Authorization header, cookies, access/refresh tokens.
The shared logger already redacts secrets (see `app/core/logging.py`).

---

## Development Bootstrap

`scripts/bootstrap_tenant.py` grants any identity membership in any org
idempotently:

```bash
python -m scripts.bootstrap_tenant --subject <nhost-subject> --organization-id <id>
```

Re-running is a no-op (`ON CONFLICT DO NOTHING`). It validates that the
organization exists. No personal subject values are committed to migrations or
source code.

---

## Tests

`tests/test_tenant.py` — deterministic, uses synthetic subjects (`user-alpha`,
`user-beta`), never touches live Nhost, and cleans up every organization it
creates. Covers:

- one-membership auto-resolution
- zero-membership denial
- cross-tenant denial (user A cannot resolve user B's org)
- forged `X-CXOps-Organization-ID` rejection (403)
- valid requested organization acceptance
- duplicate membership blocked by the DB uniqueness constraint
- proof endpoint: unauthenticated 401 / no-membership 403 / valid 200
- multiple-membership: 409 without selector, 200 with a valid one
- no JWT/token persisted in membership rows

---

## Out of Scope (deferred)

- RBAC roles and permissions → **Phase 1D**
- Organization switcher UI / browser storage → **Phase 1C.4**
- Tenant-scoping all repositories and routes → **Phase 1C.2+**
- JWT (Nhost) configuration changes — untouched

---

## Manual End-to-End Tenant Proof (through the BFF)

Run against the real authenticated Nhost test user. Replace `<SUBJECT>` below
with the test user's `sub` claim obtained at runtime (e.g. from the FastAPI
`auth_success` log line or an inspect on the JWT) — never commit or document
the literal UUID.

**Prerequisites**

1. Local Postgres migrated to head: `alembic upgrade head`
2. FastAPI running: `uvicorn app.main:app --port 8000`
3. Next.js dev server running: `npm run dev` (BFF proxies to `BACKEND_API_URL`, default `127.0.0.1:8000`)
4. Browser logged in to Nhost so the BFF has a server session

**Setup** (create orgs via the Control Center, then grant the test user):

```bash
python -m scripts.bootstrap_tenant --subject <SUBJECT> --organization-id <ORG_A>
```

Then exercise the proof endpoint through the BFF and observe the response in
the browser/network tab at `/api/backend/me/tenant`:

| Case | Memberships | `X-CXOps-Organization-ID` | Expected |
|------|-------------|---------------------------|----------|
| 1    | one (`ORG_A`)              | absent            | `200` `{"organization_id": <ORG_A>, "organization_name": ...}` |
| 2    | one (`ORG_A`)              | forged (`999999`) | `403` |
| 3    | two (`ORG_A`, `ORG_B`)     | absent            | `409` |
| 4    | two (`ORG_A`, `ORG_B`)     | `<ORG_B>` (valid) | `200` `{"organization_id": <ORG_B>, ...}` |

Set the header via the browser dev tools (e.g. `x-cxops-organization-id: 999999`)
or a temporary `fetch` in the console. For cases 3–4 add a second membership:

```bash
python -m scripts.bootstrap_tenant --subject <SUBJECT> --organization-id <ORG_B>
```

Cleanup after the proof: delete the throwaway membership directly in SQL
(`DELETE FROM organization_memberships WHERE organization_id = <ORG_B> AND subject = '<SUBJECT>';`).

These cases prove the browser may request an org id, the BFF forwards it
unchanged, FastAPI validates membership, forged ids return 403, a missing
selector with one membership auto-resolves, and a missing selector with
multiple memberships returns 409. The selector itself never authorizes.

---

## pytest.ini — session-scoped asyncio loops

`pytest.ini` sets `asyncio_default_test_loop_scope = session` and
`asyncio_default_fixture_loop_scope = session`.

**Why this is required, not a fix masking an isolation defect:** the app engine
in `app/core/database.py` is a process-global `AsyncEngine`, and its asyncpg
connection pool hands out connections bound to the event loop that created
them. Under the pytest-asyncio default (a fresh loop per test) a connection
created in test #1 is later reused from the pool inside test #2's different
loop → `RuntimeError: Task got Future attached to a different loop`. Sharing a
single session loop makes pooling deterministic. Fixtures, sessions, and rows
are still created and torn down per test (tests create unique organizations and
remove them), so this is purely an event-loop concern and does not hide state
leakage between tests.