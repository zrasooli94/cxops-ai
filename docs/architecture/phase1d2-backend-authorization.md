# Phase 1D.2 — Backend Route & Service Authorization

Status: **IMPLEMENTED** (pending final review) · Phase: 1D.2 · Depends on: Phase 1D.1 (complete)

## Objective

Apply the Phase 1D.1 capability model to **every human/JWT-backed route** in
the CXOps backend, add **service-level defense in depth** for sensitive
operations, emit **audit logging on every denial**, and lock behavior in with
**role-aware HTTP tests**. Machine-only paths (signed webhooks, Zendesk OAuth
callback, integration/agent workers, internal automation execution) are
explicitly **exempt from human role checks** and must keep working untouched
by role requirements.

Scope guardrails:

- No redesign of the 1D.1 role/capability matrix unless a real conflict arises.
- No frontend role-aware navigation. 403s are acceptable; only minimal generic
  forbidden handling if a page breaks.
- No member-management UI. No per-agent-tool RBAC (that is Phase 1D.3).
- No commits. Stay inside `~/FreeProjects/cxops-ai`.

## Capability inventory (source of truth: `app/core/rbac.py`)

```
organization.read | organization.manage | member.read | member.manage
customer.read  | customer.write
ticket.read    | ticket.write
agent.run      | agent.approve | agent.execute
knowledge.read | knowledge.manage
automation.read| automation.manage
integration.read | integration.manage
observability.read
```

There is **no** `automation.write`, `knowledge.write`, or `agent.read` — write
is expressed as `*_manage` / `agent.execute`. The agent run *list/get/events*
routes therefore use `agent.run` (documented choice below).

## Route → capability matrix

Primary enforcement is centralized via the existing dependencies:

- `CurrentAuthorization` — `app/api/deps.py:185`
- `RequireCapability(capability)` — `app/api/deps.py:191`

Every human route below gains a `RequireCapability(...)` dependency. Tenant
resolution (`CurrentTenant`) is the outer gate and always resolves **before**
capability resolution, so tenant selection can never be used to bypass
authorization.

### Organizations (`app/api/routes/organizations.py`)

| Route | Capability | Notes |
|---|---|---|
| POST `/organizations` | *(none — bootstrap)* | Organization does not exist yet; principal-only, creator becomes owner (1D.1). Documented exception. |
| GET `/organizations` | `organization.read` | |
| GET `/organizations/{id}` | `organization.read` | |

### Customer / ticket (read/write)

| Route | Capability |
|---|---|
| POST `/customers` | `customer.write` |
| GET `/customers` | `customer.read` |
| GET `/customers/{id}` | `customer.read` |
| PATCH `/customers/{id}` | `customer.write` |
| POST `/tickets` | `ticket.write` |
| GET `/tickets` | `ticket.read` |
| GET `/tickets/{id}` | `ticket.read` |
| PATCH `/tickets/{id}` | `ticket.write` |

### Knowledge (`app/api/routes/knowledge.py`)

| Route | Capability | Notes |
|---|---|---|
| GET `/knowledge/documents` | `knowledge.read` | list |
| GET `/knowledge/documents/{id}` | `knowledge.read` | |
| POST `/knowledge/documents` | `knowledge.manage` | ingest (JSON) |
| POST `/knowledge/documents/upload` | `knowledge.manage` | file upload |
| POST `/knowledge/search` | `knowledge.read` | |
| POST `/knowledge/answer` | `knowledge.read` | |
| DELETE `/knowledge/documents/{id}` | `knowledge.manage` | |

### Automation rules (`app/api/routes/automation_rules.py`)

| Route | Capability |
|---|---|
| POST `/automation-rules` | `automation.manage` |
| GET `/automation-rules` | `automation.read` |
| GET `/automation-rules/{id}` | `automation.read` |
| PATCH `/automation-rules/{id}` | `automation.manage` |

### Agent (`app/api/routes/agent.py`)

| Route | Capability | Notes |
|---|---|---|
| POST `/agent/tickets/{id}/analyze` | `agent.run` | |
| POST `/agent/runs/{id}/approve` | `agent.approve` | |
| POST `/agent/runs/{id}/reject` | `agent.approve` | |
| POST `/agent/runs/{id}/execute` | `agent.execute` | human enqueue; see service seam below |
| GET `/agent/runs` | `agent.run` | list |
| GET `/agent/runs/{id}` | `agent.run` | |
| GET `/agent/runs/{id}/events` | `agent.run` | |

**Documented choice:** there is no `agent.read`. The run list/get/events
endpoints expose run internals (tool plans, response drafts, decision events),
so they require `agent.run` — the capability that lets you *drive* the agent —
rather than `ticket.read`. Viewers and supervisors still see ticket analysis
via `ticket.read` on the ticket and via observability. This is a deliberate,
documented restriction.

### Observability (`app/api/routes/observability.py`)

| Route | Capability |
|---|---|
| GET `/observability/ai/summary` | `observability.read` |
| GET `/observability/agent/summary` | `observability.read` |
| GET `/observability/ai/by-feature` | `observability.read` |
| GET `/observability/agent/kpis` | `observability.read` |
| GET `/observability/agent/roi` | `observability.read` |

### Zendesk (`app/api/routes/zendesk.py`)

| Route | Capability | Notes |
|---|---|---|
| GET `/zendesk/me` | `integration.read` | current Zendesk user |
| GET `/zendesk/tickets/{id}` | `ticket.read` | |
| POST `/zendesk/tickets` | `ticket.write` | create |
| PATCH `/zendesk/tickets/{id}` | `ticket.write` | |
| GET `/zendesk/tickets/{id}/comments` | `ticket.read` | |
| POST `/zendesk/tickets/{id}/sync` | `ticket.write` | writes a local ticket record |
| GET `/zendesk/users/{user_id}` | `integration.read` | Zendesk-side user lookup |

### Tenant / self (`app/api/routes/tenant.py`)

| Route | Capability | Notes |
|---|---|---|
| GET `/me/tenant` | *(none)* | tenant proof |
| GET `/me/organizations` | *(none)* | **switcher — must work for every member regardless of role** (incl. viewer). Tenant UX, not org management. |
| GET `/me/authorization` | *(none)* | returns own role/capabilities; requires only `CurrentAuthorization` |

## Machine paths — explicitly exempt from human role checks

These continue to authenticate by machine mechanism and MUST NOT gain any
human-role dependency:

- `POST /webhooks/ticket-events` (`app/api/routes/webhooks.py`) — HMAC
  signature, `verify_ticket_event_signature`.
- `POST /api/webhooks/zendesk/tickets/{integration_id}` (`zendesk_webhooks.py`)
  — HMAC signature + replay window, tenant derived from the trusted connection
  row, **never** from the payload.
- `GET /auth/zendesk/login` + `/callback` (`zendesk_auth.py`) — OAuth dance.
  `/callback` is publicly reachable for the provider redirect:
  authorization is bound to the durable OAuth state
  (`state → organization_id`, one-time, never to browser claims). The login
  path is human and will be capability-gated at the *service* seam where state
  is created (see service layer), not via a JWT role header.
- Integration job worker (`IntegrationJobService.run`) — queue consumer,
  machine identity only.
- Agent background execution / automation-triggered enqueue
  (`agent_workflow_service` → `enqueue_agent_execution`) — machine path; must
  not require a human role. See service seam below.

These are currently reached only from machine callers, so they require no
change beyond a regression test that proves they carry no human-role gate.

## Service-level defense (smallest maintainable strategy)

Defense in depth for the operations the directive calls out, without pushing
role logic into low-level repositories (repositories stay tenant/data-scoped
only).

1. **Server-side checks live in the application-service layer, not routes.**
   Public sensitive service methods that are invoked directly from human
   routes take an explicit `authz: AuthorizationContext` (or a
   `RequireCapability`-equipped dependency) and call
   `require_capability(authz, Capability.X)` internally. A future route that
   forgets its dependency still cannot bypass because the service enforces it.
   Repositories are NOT touched.

2. **Agent approval** (`AgentApprovalService.approve/reject`): every caller is
   a human route. Add `require_capability(authz, AGENT_APPROVE)` inside both
   methods on a new `authz` parameter. No machine caller exists — safe.

3. **Knowledge mutation** (`KnowledgeIngestionService.ingest`): both call
   sites are human routes (`knowledge.py`). Add
   `require_capability(authz, KNOWLEDGE_MANAGE)` inside `ingest`. No machine
   callers. Document delete/upload as route-gated (`knowledge.manage`).

4. **Integration configuration** (`ZendeskOAuthService.create_state`):
   single human caller is the `/login` route. Assert
   `require_capability(authz, INTEGRATION_MANAGE)` inside `create_state`.
   `/callback` (`exchange_code`) stays machine/state-bound and is NOT
   capability-gated (provider redirect cannot carry a human JWT).

5. **Agent execution enqueue — deliberate seam.**
   `IntegrationJobService.enqueue_agent_execution` is called by BOTH the human
   `/execute` route AND the machine automation/workflow path
   (`agent_workflow_service`). It therefore must NOT carry a human-role
   capability check. Defense for this seam:
   - The human `/execute` route requires `agent.execute`.
   - The service already asserts tenant ownership of the run
     (`_assert_agent_execution_target`) and the run's status/pending-approval
     state — a machine path cannot execute a foreign tenant's run.
   - Documented in `docs` that this service is the sanctioned
     human-role-free entry for machine execution.
   This is the smallest strategy that keeps the automation/agent worker
   pipeline working per the "machine paths must not depend on human role"
   requirement.

6. Sensitive services that were already tenant-scoped and move **no** data
   across tenants (customer/ticket/automation services read
   `organization_id` from `CurrentTenant`, never from client payload) are left
   unchanged; their authorization lives at the route + capability boundary.

## Denial semantics (fail closed, no information leak)

- 401 — missing / invalid authentication (existing).
- 403 — capability denied, missing membership, forged/never-present tenant
  selector, or role invalid after resolution. **Never** downgrade an
  authorization failure to 404.
- 409 — ambiguous tenant (multiple memberships, no selector) — existing
  `CurrentTenant` behavior.
- 404 — only for genuinely-absent tenant-owned resources *after* capability
  passes (no resource enumeration across tenants).
- No leaking of the full capability matrix in any error detail; generic
  "Insufficient permissions" (already the deps behavior).

## Audit logging

`RequireCapability` (app/api/deps.py:191) already logs `capability_denied`
with subject / organization / role / capability. Extend it (and any new
service-side `require_capability` call sites share the same log helper in
`app/core/rbac/audit`) to include:

- `decision=denied`, `route`/`action`, `request_id` (from the existing request
  correlation middleware).
- Never log: JWT, cookies, tokens, secrets.
- No per-success GET logging for authorization (no noisy success audit).

## Tests

New file: `tests/test_phase1d2_authorization.py` (reuse the token/tenant/org
helpers from `tests/test_rbac.py`; do not modify existing suites). Coverage:

1. **Role-aware HTTP matrix** — for each route+role pair assert
   `200/201/202` vs `403` per the matrix, using owner/admin/supervisor/agent/
   viewer. Includes viewer read 200 + viewer write 403; supervisor
   `automation.read` 200 but `automation.manage` 403; agent `agent.run` 200.
2. **Cross-org role test** — same subject is admin in Org A and viewer in Org
   B: admin sees A's data, viewer capabilities enforced against B (403 on
   B-write), and `GET /me/organizations` returns both.
3. **Tenant forgery / selector** — `X-CXOps-Organization-ID` pointing at a
   non-member org → 403; missing selector with multiple memberships → 409;
   no membership → 403.
4. **Role/capability spoofing** — forged `role`, `Authorization`, or
   capability claims in headers / query / body are ignored (role comes from
   the DB membership row, not the token) and denied fail-closed (403).
5. **Capability-regression tests** — a test that fails if a
   `RequireCapability(...)` dependency is removed from a known-gated route
   (viewer must get 403; removing the dep makes the test fail).
6. **Service-bypass regressions** — calling `AgentApprovalService.approve`,
   `KnowledgeIngestionService.ingest`, and `ZendeskOAuthService.create_state`
   without the required capability raises the RBAC denial, so a future
   unguarded route cannot bypass via the service layer.
7. **Machine-path regressions** — signed webhook + Zendesk OAuth callback +
   worker enqueue succeed with NO human role dependency (e.g. caller that has
   no membership/role still passes the machine-authenticated path).

## Docs

`docs/architecture/phase1d2-backend-authorization.md` (this file) and a
`HISTORY`-style note added to the existing Phase 1D docs. Reference the
matrix, the seams, and the denial semantics.

## Verification (exact)

Backend:
```
.venv/bin/python -m pytest -m "not integration" -q
.venv/bin/ruff check app tests scripts
.venv/bin/mypy app tests
.venv/bin/alembic check && .venv/bin/alembic current   # head unchanged, 1d100001
```
(new `tests/test_phase1d2_authorization.py` included in the pytest run; all
existing Phase 1B/1C/1D.1 suites — test_rbac, test_cross_tenant,
test_agent_tenant, test_knowledge_tenant, test_observability_tenant,
test_zendesk_tenant — stay green.)

Frontend (unchanged behavior expected; run to prove no regressions):
```
npm run test:auth && npm run test:session
npx next typegen && npx tsc --noEmit && npm run lint && npm run build
```

`git diff --check` clean. `git status` clean (nothing committed).

## Delivery

Apply edits, run the full verification, then run `ocr delegate preview` for
the independent adversarial review (zero Critical/High) before reporting
READY FOR REVIEW. No commits.

## History

- **2026-09-16** — Implemented.
  - Added `RequireCapability` guards to all human/JWT routes in
    `app/api/routes/{customers,tickets,automation_rules,organizations,knowledge,observability,zendesk,zendesk_auth,agent}.py`
    using module-level `Annotated` aliases (`XxxAuthz = Annotated[AuthorizationContext, Depends(RequireCapability(Capability.XXX))]`),
    matching the existing `CurrentPrincipal`/`CurrentTenant` idiom and keeping
    ruff B008-clean.
  - Hardened `RequireCapability` in `app/api/deps.py` to log denied requests
    with `request_id` and `route`, and fixed its docstring to document the
    safe plain-annotation / Annotated-alias patterns (the previous example
    combined `CurrentAuthorization` with a default `Depends`, which bricks
    FastAPI at app build).
  - Added service-layer defense in depth:
    - `AgentApprovalService.approve/reject` require `AGENT_APPROVE`.
    - `KnowledgeIngestionService.ingest` requires `KNOWLEDGE_MANAGE`.
    - `ZendeskOAuthService.create_state` requires `INTEGRATION_MANAGE`.
  - Left `IntegrationJobService.enqueue_agent_execution` intentionally
    ungated (human `/agent/runs/{id}/execute` requires `AGENT_EXECUTE`; machine
    automation/agent-worker path uses the same service).
  - Added a global `AuthorizationError` → 403 handler in `app/main.py` so
    service-layer denials surface correctly.
  - Added `tests/test_phase1d2_authorization.py` with role-matrix, cross-org,
    service-bypass, and machine-path regression tests.
  - Updated `tests/test_rbac.py` and `tests/test_knowledge_tenant.py` for the
    new `RequireCapability`/`ingest` signatures.
  - Verification: pytest 418 passed, ruff clean, mypy clean, alembic head
    unchanged, frontend tests/build green, `git diff --check` clean.
