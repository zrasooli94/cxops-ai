# Phase 1Q — Commercial SaaS launch and customer onboarding

Status: implemented on `feat/phase1q-commercial-saas-foundation` (draft PR, not
deployed). Nothing in this phase changes production infrastructure, secrets,
data, DNS, Nhost, Render, Vercel, Neon or OpenAI configuration. Every
production step is listed, with its approval gate, in
[the launch runbook](../runbooks/phase1q-commercial-launch.md).

Base: `master` @ `32dc64f` (production release). Migration head moves
`1p9b0001` → `1q0a0001` (additive).

Legend used throughout: **Implemented** (code + tests on this branch),
**Existing** (shipped before 1Q, reused), **Proposed** (designed or
documented, not built), **Gated** (built, off until an owner approval).

---

## 1. Audit: what CXOps already provided (Workstream A)

### Feature-gap matrix (before → after Phase 1Q)

| Capability | Before 1Q | Gap found | Phase 1Q outcome |
|---|---|---|---|
| Public website | Engineering-demo homepage ("demonstration / portfolio"), `/platform` overview, buyer's guide | Not sales-ready; links into login-only pages; no lead capture | **Implemented**: Home, Features, How it works, Solutions, Request a demo, FAQ; `/platform` → 301 `/features`; guide re-worded |
| Nhost authentication | **Existing**, operational (JWKS in prod) | No sign-up in the app (invitees need an Nhost account) | Reused unchanged. Account creation for invitees is an **owner decision** (§9) |
| Organizations | **Existing**: any signed-in user could `POST /organizations` and became owner of an immediately fully-capable tenant | No lifecycle, no approval, self-created tenants could reach AI spend and external actions | **Implemented**: `lifecycle_status` (onboarding → pending_review → active / suspended); self-service orgs start in `onboarding` |
| Memberships / RBAC | **Existing**: 5 roles, capability matrix, fail-closed resolution, tenant selector header validated against membership | No team-management API; no invitations; no platform-admin concept | **Implemented**: team API, single-use invitations, role-rank rules, platform operators (separate from tenant RBAC) |
| Tenant onboarding | **Existing**: reviewed YAML manifest + `scripts/onboard_tenant.py` (operator only) | No customer-facing setup | **Implemented**: setup wizard over the existing models; manifest path unchanged |
| Widget config & install | **Existing**: one config per tenant (unique), exact-origin allowlist, hashed key shown once, rotation, kill switch, loader script | Open sessions kept chatting after the widget was disabled (`send_message` did not re-check `enabled`); `frame-ancestors` list hard-coded (new customer = code change) | **Fixed**: every message re-checks enabled + lifecycle; **Implemented**: env-extensible `CXOPS_WIDGET_FRAME_ANCESTORS` |
| Knowledge base | **Existing**: tenant-filtered ingest/search/RAG (pgvector) | Ingestion calls paid embeddings for any tenant | **Gated**: unapproved tenants have no knowledge capability unless `ONBOARDING_KNOWLEDGE_SETUP_ENABLED` |
| Public chat config | **Existing** staff settings page (`/settings/public-chat`) | Not in navigation; duplicate provider rows (one per tool) | **Implemented**: nav entry; provider list de-duplicated (regression test) |
| Inbox / handoff | **Existing**: handoff queue, assign/release/resolve, 15 s refresh | — | Reused; linked from the owner overview |
| AI metering & allowances | **Existing**: per-call cost estimates, monthly turn/$ allowance per tenant, platform ceiling, reservations | Allowance editable only by DB/CLI; no owner view; no platform view; non-approved tenants could spend | **Implemented**: owner usage card, operator entitlements + platform cost view; reservation refused for non-active tenants |
| Commercial-readiness docs | Phase 1P.9 / 1P.9R runbooks | No customer journey, no operator process, no pricing decisions | **Implemented**: this document + runbook + owner guide |
| Billing / payments | None | — | **Proposed only** (§8). No Stripe, no prices, no charges |
| Invitation email delivery | None | — | **Proposed** (§9); links are shared manually today |

### Operational vs incomplete vs demonstration-only (after 1Q)

* **Operational (tested locally end to end):** lead capture (flag-gated),
  operator review/provisioning/approval, invitations, setup wizard, widget
  creation and install, owner overview, team management, lifecycle gating,
  usage visibility.
* **Incomplete:** invitation email delivery, in-app sign-up, billing, editable
  plan catalogue, server-side pagination of the operator tenant list.
* **Demonstration-only (pre-existing, unchanged):** transformation,
  simulations, experiments and the A1 automotive pilot pages remain in the
  operations navigation; A1 integration is `local_demo`.

## 2. Architecture

```
            ┌──────── public website (static, Server Components) ────────┐
visitor ──▶ │ / features how-it-works solutions faq early-access         │
            └──────────────┬──────────────────────────────────────────────┘
                           │ POST /api/public/leads (BFF, no cookies forwarded)
                           ▼
FastAPI  /public/leads ── flag ─ honeypot ─ rate limits ─ sales_leads (one open lead per email)

operator ──▶ /operator (session) ──▶ /api/backend/platform-admin/*  ── RequirePlatformOperator
             leads: new→reviewing→contacted→approved/declined           (platform_operators by verified sub)
             provision(approved lead) → organizations(onboarding) + owner invitation (token once)
             tenants: approve / request changes / suspend / reinstate; entitlements; platform AI cost

owner ───▶ /invite#token (fragment) ─▶ /invitations/accept ─▶ organization_memberships(subject=sub)
           /setup ─▶ /onboarding/* (organization.*)   widget created disabled, key shown once
           /team  ─▶ /team/*       (member.*) + role-rank rules
           /workspace ─▶ /workspace/overview (sections by effective capability)
           /settings/public-chat (existing; enabling requires lifecycle=active)
```

### The lifecycle gate (central, fail-closed)

`AuthorizationContext.capabilities = role capabilities ∩ lifecycle mask`
(`app/core/rbac.py`). The role in the membership row is untouched; only the
effective set narrows until approval:

| Status | Effective capabilities |
|---|---|
| `active` | everything the role grants |
| `onboarding`, `pending_review`, unknown | organization.read/manage, member.read/manage, customer.read, ticket.read, integration.read, observability.read, automation.read (+ knowledge.read/manage only if `ONBOARDING_KNOWLEDGE_SETUP_ENABLED`) |
| `suspended` | organization.read, member.read |

Because every existing route already declares a capability, this one change
withholds agent runs, evaluations, knowledge search/ingestion, integration
management (Zendesk OAuth, business providers), ticket/conversation writes and
automation changes from unapproved tenants **without touching those routes**.
Defence in depth on the money and external-action paths:

* `TenantConfigService.update_public_chat(enabled=True)` and
  `set_business_integration(enabled=True)` refuse non-active tenants.
* `PublicChatService._require_live` (session create + **every** message):
  widget enabled **and** organization active.
* `TenantAIAllowanceService.reserve` refuses non-active tenants under the
  budget lock (reason `organization_not_active` → human handoff, no model).

### Platform operator authority

`platform_operators(subject, granted_by, revoked_at, …)`, partial unique on
active subject. Granted **only** by `scripts/manage_platform_operators.py`
(dry-run default, UUID subjects only, no email). No HTTP endpoint can grant,
extend or revoke it; tenant roles, selector headers, emails and JWT role
claims are never consulted. Revocation is effective on the next request.

### Data model (migration `1q0a0001`, additive)

* `organizations`: `lifecycle_status` (NOT NULL, server default `active` → every
  existing tenant, incl. RISPU, keeps its behaviour), `website_url`,
  `plan_code`, `submitted_for_review_at`, `lifecycle_changed_at/by`, check
  constraint.
* `organization_memberships.email` (nullable, display only).
* `platform_operators`, `sales_leads` (partial unique open-lead-per-email),
  `organization_invitations` (unique token hash; partial unique pending per
  org+email).

Verified on the disposable database: upgrade → downgrade → upgrade, and
`alembic check` reports no drift.

### API surface (new)

| Route | Auth | Notes |
|---|---|---|
| `GET /public/leads/availability`, `POST /public/leads` | none | flag, 202 generic response, 503 when off, 429 on limits |
| `/platform-admin/{me,leads,leads/{id},leads/{id}/provision,organizations,organizations/{id}/lifecycle,organizations/{id}/entitlements,usage}` | platform operator | |
| `/onboarding/{state,business,widget,submit}` | organization.read / manage | no organization id in any body; `extra="forbid"` |
| `/team/{members,members/{id},invitations,invitations/{id}/revoke}` | member.read / manage (+ any member may leave) | |
| `/invitations/{preview,accept}` | authenticated principal, no tenant | token in body, never URL |
| `/workspace/overview` | any member | sections gated by effective capability |

### Frontend

* `(marketing)` route group: shared header/footer, Server Components, zero-JS
  mobile menu (`<details>`), JSON-LD FAQ, sitemap/robots updated.
* Control center: **Your business** group — Overview `/workspace`, Setup
  `/setup`, Team `/team`, Chatbot settings `/settings/public-chat`; lifecycle
  notice in the sidebar and the mobile bottom bar; first mobile navigation for
  the control center (the existing pages own a fixed top header, so mobile nav
  lives in the bottom bar).
* `/invite` and `/operator` live outside the control-center layout (an invitee
  has no membership yet; an operator need not belong to any tenant).
* Widget: three self-service palettes (`midnight`, `ocean`, `forest`) added to
  the closed theme allowlist; tenant-specific palettes (RISPU) are not
  selectable in setup.

## 3. Security review (Workstream H)

| Requirement | Control | Evidence |
|---|---|---|
| Tenant A cannot read/change Tenant B | membership-bound tenant resolution (existing) + every new query bound to the resolved org; path ids from another tenant → 404 | `test_team_endpoints_are_tenant_bound`, `test_multi_company_user_switches_without_leakage`, E2E outsider check |
| Widget key ≠ staff credential | staff routes require a verified JWT; key is only a hash lookup on public routes | `test_widget_key_is_not_a_staff_credential` |
| Tenant owner ≠ platform admin | separate table by verified `sub`; CLI-only grants | `test_tenant_owner_is_not_a_platform_operator`, `test_operator_cannot_be_granted_by_email_claim`, browser check (owner sees "operators only") |
| Org selection cannot bypass membership | existing selector validation; new endpoints use the same dependency | header-for-other-tenant → 403 tests |
| Unapproved tenants cannot enable chatbots | lifecycle mask + service checks + `_require_live` + reservation | `test_unapproved_tenant_widget_never_serves_visitors`, `test_enabling_widget_or_integration_requires_approval`, `test_ai_reservation_refused_for_unapproved_or_suspended_tenant`, browser: 403 before approval |
| Invitation tokens unusable twice / unguessable | 256-bit `token_urlsafe`, SHA-256 at rest, conditional UPDATE (pending ∧ unexpired), generic 404 | `test_invitation_accept_is_single_use`, `test_invitation_tokens_cannot_be_guessed_expired_or_revoked`, `test_concurrent_acceptance_creates_one_membership` |
| No privilege escalation | rank rules in service; no self role change; last-owner protection; member.manage re-checked in service | `test_privilege_escalation_is_refused` |
| Billing/allowance not tenant-editable | allowance fields absent from every tenant contract (`extra="forbid"`); operator-only endpoint | `test_operator_lifecycle_and_entitlements` (422 on 3 tenant routes) |
| Domain allowlist not bypassable | existing exact-match check (server); onboarding re-validates; frame-ancestors env parser rejects wildcards/http/paths | origin tests; `commercial.test.ts` frame-ancestors cases; browser: other origin 403 |
| Cross-tenant retrieval impossible | existing tenant-filtered vector search (unchanged); knowledge capability masked pre-approval | existing `test_knowledge_tenant.py` (unchanged, passing) |
| Public forms validated and rate-limited | strict schema, honeypot, platform/hour + email/day ceilings, body size cap in BFF | lead tests (validation, honeypot, limits, concurrency) |
| No sensitive values in logs/responses | logs carry ids/field names/outcomes only — never emails, names, tokens, keys; invitation list never returns token; onboarding state never returns key/hash | assertions in invitation and wizard tests |
| Expired/revoked access enforced | invitation expiry/revocation; operator revocation; membership removal → next request 403; suspension → open sessions refused | revocation/suspension tests |
| Concurrent onboarding → no duplicates | unique constraints (one widget per org, one open lead per email, one pending invite per org+email) + row locks on provisioning | `test_concurrent_widget_creation_yields_one_configuration`, `test_concurrent_provisioning_creates_one_organization`, `test_concurrent_duplicate_lead_submissions_create_one_lead` |

Residual risks (accepted, documented):

1. **Lead-form flood can exhaust the platform hourly ceiling** (denial of the
   form, not of the product). The API cannot trust client IPs behind the BFF;
   an edge rule (Vercel Firewall) or a CAPTCHA is the upgrade — both are
   paid/third-party decisions.
2. **Invitation links travel by the operator's/owner's own channel** until
   email delivery exists. The token sits in the URL fragment (never sent to
   servers), is single-use and expires in 72 h. When the identity provider
   supplies an `email` claim, it must match the invited address.
3. **Install snippet without SRI**: the loader is first-party and changes on
   every deploy; pinning a hash would break installs.
4. **Operator tenant list is unpaginated** (fine for tens of tenants).

## 4. Tests and certification (Workstream I)

| Check | Result |
|---|---|
| Backend pytest, Python 3.12 (disposable Postgres, `-m "not integration"`) | 2266 passed (final sequential run; see PR) |
| Backend pytest, Python 3.11 | 2265 passed (before the last 1-test addition) |
| New Phase 1Q backend tests | 34 + 1 E2E journey + 2 CLI |
| Ruff / mypy (`app`) | clean |
| Frontend unit (`npm test`, all) | 794 passed |
| `test:auth` / `test:session` (CI) | 618 / 100 passed |
| ESLint, `next typegen` + `tsc --noEmit` | clean |
| `next build` | passes; marketing pages static |
| Docker: API image | built |
| Docker: frontend image | see PR (registry pull timeout locally on first attempt) |
| Migration up/down/up + `alembic check` | clean |
| Browser (real pane): desktop + 375 px mobile, no horizontal overflow on any public page | verified |

### First-load JavaScript (same machine, `next start`)

| Route | master (raw / gzip KiB) | Phase 1Q (raw / gzip KiB) |
|---|---|---|
| `/chat/embed` (widget) | 576.0 / 176.8 | 578.3 / 177.1 (+0.2 %, theme palettes) |
| `/` | 566.9 / 174.5 | 566.9 / 174.5 (HTML 41 → 79 KiB) |

gsap/lenis remain absent from every initial load; the 1P.9R widget
optimisation is intact (`embed-delivery.test.ts` still passes).

### Simulated customer journey (all local, synthetic, fake AI)

Run twice: as `tests/test_phase1q_e2e_journey.py` (in-process) and by hand in
the browser against a local API (fake model/embeddings), `next dev` and a
local test website on `http://localhost:4173`:

1. Visitor submits a demo request (validation shown inline, including a
   server-side field error mapped back to the form).
2. Operator reviews: new → reviewing → approved; provisions the workspace
   (onboarding) and receives the one-time owner link.
3. Owner (different identity) is refused the operator console; accepts the
   invitation; the token is stripped from the address bar.
4. Setup: business pre-filled from the lead; wildcard origin refused; local
   origin added; chatbot created with the Forest theme; key shown once in the
   install snippet; submitted for approval.
5. Before approval the installed widget's config request returns **403**.
6. Operator approves; owner adds a knowledge document (fake embeddings) and
   switches the chatbot on.
7. Visitor on the local site chats; the turn runs (fake model) and is handed to
   the team by the existing grounding policy.
8. Owner overview: chatbot Live, installation detected, 1 waiting handoff,
   1 document, 1 conversation, usage 1/3000 turns, $0.0001 estimated.
9. Owner invites a support agent; the link is shown once; status Pending.

Interrupted/failed paths covered: lost widget key (second create → 409, no
key), provisioning name clash (409 then success), wrong-origin visitor (403),
expired/revoked/reused invitations, onboarding resumes at the first open step.

## 5. Backward compatibility

* Existing organizations default to `active`: no capability changes for RISPU
  or any current tenant. `AuthorizationInfo.organization_status` and
  `OrganizationRead.lifecycle_status` are additive fields.
* **Behaviour change (intended):** a *new* self-service organization
  (`POST /organizations`) starts in `onboarding`.
* **Behaviour change (fix):** an open public-chat session can no longer send
  messages after the widget is disabled or the tenant suspended (403, same as
  a new session). Previously only new sessions were refused.
* The operator manifest (`scripts/onboard_tenant.py`) is unchanged and still
  creates active tenants (it is itself a reviewed operator action).
* `EXPECTED_ALEMBIC_HEAD` in `scripts/validate_production_config.py` is now
  `1q0a0001`; deploying this build requires the migration first (runbook).

## 6. Rollback

The migration is additive and the previous release ignores the new columns and
tables, so a code rollback to `32dc64f` works against a migrated database.
`alembic downgrade 1p9b0001` removes everything 1Q added (and the leads,
invitations and operator grants with it) — only if that data is to be
discarded. See the runbook for the exact order.

## 7. AI cost readiness (Workstream G)

* Owner: month-to-date AI conversations, remaining allowance, estimated cost vs
  budget, limit-reached explanation, 24 h turn health.
* Operator: per-tenant turns/cost/budget, platform ceiling usage, entitlements
  (`plan_code`, turn limit, $ budget).
* Every figure is labelled **estimated**; the provider invoice is
  authoritative. Embeddings remain unmetered (fixed per-turn estimate, 1P.9
  follow-up).

## 8. Future subscription lifecycle (Proposed — not built)

```
lead → approved → onboarding → active(trial|pilot) ──▶ active(paid plan)
                                   │                        │ payment failure
                                   └── suspended ◀──────────┘ (grace period, then suspend)
```

`plan_code` is the hook: a future billing integration would map a plan to an
allowance and set `plan_code` + allowance through the same operator path.
Requires owner decisions on prices, trial terms, payment provider, tax and
invoicing — none are made here.

## 9. Owner decisions still required

See the runbook §"Decisions and approvals" for the full list (pricing, lead
release, invitee account creation, email delivery, knowledge-before-approval,
domain onboarding process, retention of lead data, first operator grant).
