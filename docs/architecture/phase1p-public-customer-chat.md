# Phase 1P — Production Web Chat & Customer Session Gateway

Revision 1 (final — Phase 1P.1 implemented and validated end to end,
uncommitted).

Backend: config/session models + `1p1a0001` migration (single head), bound
routes, service with handoff-only reply selection, in-memory rate limiter,
scoped response headers, and `scripts/create_public_chat_config.py`. Frontend:
public BFF proxy, `/chat/embed` page + widget, `public/embed/loader.js`,
`src/lib/public-chat` helpers + tests, `/chat/embed` security header and robot
exclusion. All validation gates green (details in the session report).

## 1. Status

- COMPLETE (Phase 1P.1); pending the owner's review/commit of the uncommitted tree.
- Phase 1P.2 deliberately NOT started: no A1 quote/pickup/inventory, no RISPU
  tools, no payment/refund, no CRM writes, no WhatsApp/Messenger/IG/voice, no
  live-agency console redesign, no attachments, no customer login, no
  multilingual, no analytics redesign.

## 2. Purpose (Phase 1P.1 scope)

A tenant-aware, embeddable customer chat channel that lets a visitor start a
web-chat session, send messages, and receive AI-assisted responses backed by the
existing Phase 1K agent workflow — while **never** exposing staff authorization
to a browser and **never** allowing customer text to authorize tools.

Everything the widget does is funneled through the existing, already-audited
agent entry point:

```
agent_workflow_service.analyze(
    db, ticket_id=<web-chat ticket>, organization_id=<resolved tenant>,
    allow_auto_queue=True, persist_run=True, authz=None)
```

`authz=None` is the security keystone: `AgentWorkflowService` forces
`auto_queued = False` when no `AuthorizationContext` is present
(`app/services/agent_workflow_service.py`), so a public session can never
auto-execute tools. Phase 1P.1 is a **handoff-only intake channel**: the repo's
tool policy marks every customer-facing action (`respond` / `route` /
`escalate`) as requiring human approval, so the widget never receives live
model text — any such decision surfaces in the widget as a fixed handoff
message to staff, and only no-op decisions return the fixed fallback. The
human approval/execution path (`ToolAuthorizationService`) remains
authoritative and untouched.

## 3. Security / Public Trust Model

- **A public widget key identifies a tenant's chat configuration but does NOT
  grant staff access or tool authorization.** It is treated as public
  (embedded in customer-facing HTML). It is only a lookup key that resolves to a
  tenant-owned `PublicChatConfiguration`.
- The backend **never** accepts `organization_id` or any tenant id from the
  browser. Tenant resolution happens server-side: the client sends
  `public_widget_key`; the backend hashes it and looks up the configuration by
  the SHA-256 hash of the key. Raw keys are never stored.
- Session tokens are end-to-end random (32 bytes) and stored only as SHA-256
  hashes. No plaintext token and no PII is ever persisted in the session row.
- Every request is bound to the tenant of the resolved configuration/session;
  repository reads are always tenant-scoped.
- Embedding origins are an exact-match allowlist (`allowed_origins`), evaluated
  byte-for-byte (scheme + host + port), no substring/prefix matching. The origin
  travels in `X-Embedding-Origin` (derived by the widget from
  `document.referrer`); it is validated on session creation and on every message.
- No CORS middleware is added globally or for the public API; the widget runs
  same-origin against the CXOps frontend BFF, which proxies to the backend. The
  only cross-origin surface is the embeddable iframe URL and the embed loader
  script, both served from the CXOps origin.
- Public API responses are bounded: only configured text, the session status,
  and public conversation messages. Internal fields (`reviewer_note`,
  `tool_plan`, `workflow_path`, decision dict, authorization digests, event
  data) are never serialized to the widget.
- Customer text can never authorize a tool. Proven both structurally
  (`authz=None`) and by a dedicated prompt-injection test (send text that tries
  to flip policy/approval fields and assert the persisted run/plan is unchanged
  and no execution is queued).
- Public API responses set `Cache-Control: no-store`; public widget pages opt
  out of search indexing; no `dangerouslySetInnerHTML` anywhere in the widget.

## 4. Architecture

```
customer site                     cxops frontend                    cxops backend
  <script /embed/loader.js>
  -> builds <iframe /chat/embed?key=pk_live_...> (cxops origin)
                                    /chat/embed (server component, forbids robots)
                                    widget (client) computes embedding origin
                                    from document.referrer
                                    /api/public/chat/[...path]  (public BFF proxy,
                                       NO staff auth, NO cookies/nhost)
                                                       proxied ->  /public/chat/* (no control-center auth)
                                                              POST /sessions  -> resolve key hash + origin allowlist
                                                              POST /messages  -> verify token hash + expiry + rate limit
                                                                               + idempotent message insert
                                                                               + analyze(authz=None)
                                                                               + bounded reply / handoff
                                                               GET  /state     -> bounded public history + status
                                                               POST /human     -> human_requested
                                                               POST /close     -> closed
```

- `PublicChatConfiguration` (tenant-owned): widget key hash, display name,
  welcome message, enabled flag, exact allowed-origin allowlist, comfort
  limits, session TTL.
- `PublicChatSession` (tenant-owned): token hash, status, expiry, and a
  tenant-safe link to the conversation + ticket + optional customer.
- The web-chat conversation uses the existing `Conversation`/
  `ConversationMessage` tables: `provider="cxops"`, `channel="chat"`,
  `external_thread_id=NULL`, plus the normal tenant-safe composite FKs. The
  bounded message limits, `ticket_initial:`/dedupe conventions and tenant-safe
  customer linkage are all reused unchanged.

## 5. Data Model (new migration off `c3f9a1d2b7e4`)

### `public_chat_configurations`

| column | type | notes |
| --- | --- | --- |
| id | int PK | |
| organization_id | int NOT NULL | FK `organizations.id` |
| public_widget_key_hash | varchar(64) NOT NULL | SHA-256(hex) of the public key; unique index |
| display_name | varchar(100) NOT NULL | |
| welcome_message | varchar(500) NOT NULL | |
| enabled | bool NOT NULL | default true |
| allowed_origins | jsonb NOT NULL | exact-origin allowlist, defaults `[]` |
| theme_token | varchar(100) NULL | opaque token; only known frontend mappings are rendered |
| max_message_length | int NOT NULL | default 4000; CHECK 1..10000 |
| max_messages_per_minute | int NOT NULL | default 20; CHECK 1..300 |
| session_ttl_hours | int NOT NULL | default 24; CHECK 1..168 |
| created_at / updated_at | timestamptz | |

Composite unique `(id, organization_id)` (FK target for tenant-safe session
FKs); index on `organization_id`.

### `public_chat_sessions`

| column | type | notes |
| --- | --- | --- |
| id | int PK | |
| organization_id | int NOT NULL | FK `organizations.id` |
| configuration_id | int NOT NULL | tenant-safe FK to configuration (CASCADE) |
| conversation_id | int NOT NULL | tenant-safe FK to `conversations` (CASCADE) |
| ticket_id | int NOT NULL | tenant-safe FK to `tickets` (CASCADE) |
| customer_id | int NULL | tenant-safe FK to `customers` (CASCADE); anonymous => NULL |
| token_hash | varchar(64) NOT NULL | SHA-256(hex) end-to-end random token; unique index |
| status | varchar(20) NOT NULL | `ai_active` / `human_requested` / `human_assigned` / `closed`; CHECK |
| expires_at | timestamptz NOT NULL | session-credential expiry (short-lived) |
| closed_at | timestamptz NULL | |
| created_at / updated_at | timestamptz | |

Indexes: `organization_id`, `configuration_id`, `status` (active-count guard).

All new names: `pk_*`, `ux_*`, `ix_*`, `fk_*`, `ck_*`. Downgrade drops the
session table then the configuration table. Single head.

## 6. Tenant Resolution

- Client → backend: `public_widget_key` only.
- Backend: `sha256(key)` lookup against `public_widget_key_hash`; configuration
  must exist and be `enabled`. `organization_id` is read from the row, then every
  follow-up query (session, conversation, ticket, customer, agent run) is scoped
  by `organization_id` server-side.

## 7. Session Security

- Token: `secrets.token_urlsafe(settings.public_chat_session_token_entropy_bytes)`
  (32 bytes). Stored as `sha256(token).hexdigest()`.
- `expires_at = now + session_ttl_hours`. Verification rejects expired sessions
  (401 "Session expired") and closed sessions. Expired => fail safely, a new
  session may be created, and the old conversation is never re-exposed.
- No browser-side tenant identifiers participate.

## 8. Conversation / Agent Integration

- `POST /sessions` creates everything up front: a `Ticket`
  (`source="web-chat"`, placeholder subject/description updated from the first
  message), the `Conversation` (`cxops`/`chat`, `external_thread_id=NULL`), the
  session row, and swallows commit races (rollback + re-resolve like the
  customer-identity pattern).
- `POST /messages` (idempotent, see below):
  1. verify session + origin + rate limit;
  2. insert the inbound `ConversationMessage`
     (`direction="inbound"`, `visibility="public"`,
     `dedupe_key="public_chat:<client_message_id>"`, provider `cxops`), mirror
     the ticket subject/description (first message) and conversation subject;
  3. `await agent_workflow_service.analyze(..., authz=None)`;
  4. decide reply — **handoff-only** (Phase 1P.1 is human-in-the-loop intake:
     the repo's tool policy forces `requires_human_approval` on every
     customer-facing action, so the widget never receives live model text):
     - run action is NOT in `SAFE_AUTOREPLY_ACTIONS` (`no_action`,
       `internal_note`), or the run is missing =>
       `session.status = human_requested`, reply = fixed handoff message
       "A support team member needs to review this request." This covers
       `respond` / `route` / `escalate` / `human_review`;
     - otherwise status stays `ai_active`, reply = fixed fallback text
       "Your request has been received by the CXOps team.";
  5. insert the outbound public reply
     (`dedupe_key="public_chat_reply:<client_message_id>"` so a retry returns
     the same reply);
  6. return only `{message_id, reply, status, handoff}`.
- The agent's `response_draft` is persisted in the run for staff but is never
  serialized to the widget in 1P.1. Auto-publishing a freed agent draft to the
  customer is an explicit Phase 1P.2 carve-out (new `zendesk.send_reply`-like
  public-chat policy + live-agent approval of drafts).
- History (`GET /state`) returns at most the last 50 public messages
  (`visibility="public"`) with `direction`/`sent_at`/`body`, plus session
  status. Internal-visibility messages never reach the widget.

## 9. Idempotency & Concurrency

- A `client_message_id` (≤100 chars, validated) is required per send.
- Inbound dedupe: `(conversation_id, "public_chat:<client_message_id>")` hits
  the existing `ux_conversation_messages_conversation_dedupe` unique constraint.
  Reply dedupe: `(conversation_id, "public_chat_reply:<client_message_id>")`.
- On a duplicate or an `IntegrityError` race, the service rolls back and
  re-resolves the already-persisted reply by dedupe key and returns it.
- Cross-session reuse of the same `client_message_id` cannot collide: dedupe
  keys are scoped to the conversation, and a session never sees another
  session's conversation.
- Session creation guards `max active sessions per config` (count over
  `status != closed AND expires_at > now`).

## 10. Abuse Protection

- Per-session: `max_messages_per_minute` sliding window (in-memory, per
  instance) — burst guard; plus `max_message_length`.
- Per-config: message-per-minute and new-sessions-per-hour in-memory limits;
  durable max-active-sessions DB count at session creation.
- Config disabled => 403 `{"detail": "Widget disabled"}`.
- Unknown key => 404; disabled => 403; wrong origin => 403.
- No CAPTCHA, no IP-as-tenant, no raw IP persistence (IPs are never stored).
- In-memory limits are per-process; single-instance is the deployed topology for
  this phase (documented limitation; DB-backed counters are the Phase 1P.2
  upgrade path).

## 11. Human Handoff

- States: `ai_active` (in turn; awaiting the agent decision) ->
  `human_requested` (visitor requested a human, or the agent decided a
  customer-facing action is needed) -> `human_assigned` (reserved in
  1P.1; no producer yet) -> `closed`.
- `human_requested` is recorded by the backend via the fixed handoff message;
  `closed` via the close endpoint (or after TTL validation failure).
- The conversation and ticket remain open throughout; inbox/human tooling is
  unchanged. The handoff message is fixed and bounded; no response-time
  promises are made.

## 12. Widget & Embed

- `frontend/src/app/chat/embed/page.tsx` — public server component (NOT under
  `(control-center)`, NO CapabilityRouteGuard, forbids robots via per-page
  metadata `robots: { index: false, follow: false }` and the global
  `robots.ts` disallow of `/chat/embed`).
- `frontend/public/embed/loader.js` — embed script used as
  `<script src="https://<cxops>/embed/loader.js" data-widget-key="pk_live_...">`;
  builds the iframe pointed at
  `https://<cxops>/chat/embed?key=<public key>`. The iframe host is derived
  from `script.src` (`new URL(script.src).origin`) so dev
  (`localhost:3000/embed/loader.js`) and prod (Vercel) each point the iframe at
  the same origin that served the loader. The loader starts the iframe at the
  96x96 launcher footprint and resizes it on `cxops-embed:resize` postMessage
  events from the widget (origin-validated against the loader origin; clamped
  to 520x640).
- Widget client derives `embedding_origin` from `document.referrer`
  (`new URL(document.referrer).origin`); sends it in `X-Embedding-Origin`, then
  calls the public BFF proxy (`/api/public/chat/[...path]`) same-origin.
- Rendering is React text nodes only; no raw HTML/JS from tenant config, no
  `dangerouslySetInnerHTML`.
- Widget states: launcher/panel, connecting, ai_active, waiting-for-human
  (handoff), error (backend unavailable / expired), closed. Accessible (labels,
  keyboard focus, `role="dialog"`), mobile-sized. The widget polls `GET
  /sessions/state` every 20s while `ai_active`/`human_requested` so staff-side
  status changes eventually surface in-still-sync (synchronous API; SSE is
  Phase 1P.2).
- `next.config.ts`: security headers on `/chat/embed` (`X-Content-Type-Options:
  nosniff`). No `Referrer-Policy` is set there so `strict-origin-when-cross-origin`
  keeps `document.referrer` available for the origin check.

## 13. Migration

Expected revision `1p1a0001_add_public_chat_config_and_sessions.py`,
`down_revision = "c3f9a1d2b7e4"`. Tables above, named constraints, safe
downgrade, verify `alembic heads` returns exactly one head afterwards. No
changes to unrelated tables.

## 14. Backend File Plan

- `app/models/public_chat.py` — `PublicChatConfiguration`, `PublicChatSession`.
- `app/schemas/public_chat.py` — request/response models (bounded; errors are
  `{"detail": ...}`).
- `app/repositories/public_chat_repository.py` — tenant-scoped data access.
- `app/services/public_chat_service.py` — orchestration (create/verify/append/
  run/reply/handoff/close).
- `app/services/public_chat_rate_limiter.py` — in-memory sliding windows.
- `app/api/routes/public_chat.py` — router `prefix="/public/chat"` with NO
  control-center auth dependencies; mount in `app/api/router.py`.
- `app/main.py` — scoped security headers on `/public/chat` responses
  (`Cache-Control: no-store`, `X-Content-Type-Options: nosniff`).
- `app/core/config.py` — new `Settings` fields (token entropy,
  per-config limits, max active sessions).
- `scripts/create_public_chat_config.py` — idempotent seed tool: upsert by
  org/name, generate a high-entropy `pk_live_<48 hex>` key, store only the hash,
  print the public key once (never secrets/logs the hash).
- Tests: `tests/test_public_chat.py` covering CONFIG resolution, SESSION
  create/verify/expiry, CONVERSATION wiring, IDEMPOTENCY, AGENT integration +
  handoff, ABUSE/rate limits, TENANCY (cross-tenant access denied),
  ORIGIN allowlist, PROMPT INJECTION.

## 15. Frontend File Plan

- `frontend/src/app/api/public/chat/[...path]/route.ts` — public BFF proxy
  (no staff auth, no cookies/nhost; forwards method/path/query/body +
  `X-Embedding-Origin`, 503 on backend failure). Note: an App Router segment
  cannot hold both a `route.ts` and a `page.tsx`, so robot blocking for
  `/chat/embed` lives in the page metadata + the global `robots.ts` instead of
  a separate `route.ts`.
- `frontend/src/app/chat/embed/page.tsx` + `embed.tsx` (client widget).
- `frontend/public/embed/loader.js` — embed loader (classic script, reads
  `data-widget-key`, recursion-safe postMessage resizing).
- `frontend/src/lib/public-chat/{types,client}.ts` + `client.test.ts`.
- `frontend/next.config.ts` — `/chat/embed` security headers.

## 16. Configuration/Caveats

- Widget only answers while messages are within `max_message_length`; oversize
  or empty text => 400 (no LLM cost).
- Development embeds must add their local origin to `allowed_origins` (the seed
  script documents this).
- Known limitations for Phase 1P.1: the channel is handoff-only (customer-facing
  agent decisions escalate to staff; no live model text reaches the widget —
  auto-publishing approved agent drafts is a Phase 1P.2 carve-out); message API
  is synchronous (poll for state; streaming/SSE is Phase 1P.2); in-memory rate
  limits are per-instance; no visitor login/email capture; `human_assigned` has
  no producer yet; RAG sources are not surfaced in the widget; and the ticket
  subject/description are derived from the first message.

## 17. Public Trust Statement

> A public widget key identifies a tenant's chat configuration but does not
> grant staff access or tool authorization. The key is public by design
> (embedded in customer-facing HTML); every backend decision is scoped to the
> tenant resolved server-side from that key, and the agent workflow runs
> without any `AuthorizationContext` so no tool execution can ever be
> authorized by a customer (including by crafted prompt-injection text).