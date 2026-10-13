# Phase 1P.9 — High-Performance Multi-Tenant SaaS Foundation

Status: implemented on `feat/phase1p9-saas-foundation` (draft PR, not deployed).
Nothing in this phase changes production infrastructure, secrets, plans, data,
workers or schedulers. Every activation step is listed in
[the rollout runbook](../runbooks/phase1p9-budget-and-rollout.md) with its own
owner approval gate.

## 1. Performance investigation (Workstream A)

### Observation

Live pilot visitors saw a median of about **7 s** and a p95 of about **15.5 s**
between pressing send and seeing a reply.

### Where the time went (code path before 1P.9)

`POST /public/chat/messages` did **everything inside the visitor's HTTP
request**:

| Stage | What runs | Typical cost |
|---|---|---|
| Browser → Vercel BFF | `frontend/src/app/api/public/chat/[...path]/route.ts` | 1 RTT browser→Vercel |
| BFF → Render API | server-side `fetch` | 1 RTT Vercel→Render (cross-region if Vercel functions run in `iad1` while Render/Neon are in Singapore: ~200–250 ms per RTT) |
| validate / rate-limit / dedupe | 4–6 sequential SQL statements, several committing individually | 1 Neon RTT each |
| persist inbound + ticket mirror | separate `create` + `commit` per row | 1 Neon RTT each |
| **agent decision (LLM)** | `agent_workflow_service.analyze` | **2–6 s** (gpt-4o-mini, structured output) |
| **optional grounded RAG answer** | embedding + vector search + second LLM call | **+1–5 s** |
| persist reply + log rows | more sequential commits | 1 Neon RTT each |

The model calls account for most of the median, and two sequential model calls
(decision + RAG) explain the long tail. Infrastructure adds the remainder:
cross-region function placement, Neon's scale-to-zero resume (~0.3–1 s on the
first query after 5 idle minutes on the free plan) and, if the API runs on a
free Render instance, a cold start after 15 idle minutes (tens of seconds).

Two further defects made the experience *feel* slower:

* The widget learnt about staff replies and any late AI reply only through a
  **fixed 20 s full-history poll** (`POLL_INTERVAL_MS`), so a reply that was
  ready after 3 s could appear up to 20 s later.
* `list_public_messages` returned the **oldest** 50 messages, so a long
  conversation stopped showing new messages at all (fixed; regression test
  `test_state_returns_the_latest_messages_of_a_long_conversation`).

### What 1P.9 changes

* The request returns as soon as the message is durable (`processing_state:
  "accepted"`); the model runs after the response (section 2).
* The widget polls a small cursor endpoint at the server's pace — 1 s while a
  reply is pending — instead of a 20 s full-history poll (section 3).
* Every turn logs `public_chat_timing` (stage durations) and
  `public_chat_turn_completed` (`queued_ms`, `processing_ms`), and the staff
  `GET /observability/usage` reports tenant p50/p95 turn latency for the last
  24 h, so production evidence can be gathered without ad-hoc profiling.
* `frontend/vercel.json` pins Vercel functions to `sin1` (Singapore), next to
  Render and Neon. *Activation is a deploy and therefore owner-gated.*

### Benchmark (before / after)

`scripts/benchmark_public_chat.py` drives the real app in-process against a
disposable PostgreSQL (refuses any database not named `*_disposable_test`) with
the model replaced by a fixed-latency fake (3 s, the observed median model
time). No network RTT is simulated, so absolute numbers are a lower bound; the
comparison isolates the architecture.

| Tenants sending at once | Mode | Accept p50 / p95 (ms) | Reply visible p50 / p95 (ms) | Failed requests | DB statements / message |
|---|---|---|---|---|---|
| 1 | sync (before) | 3106 / 3106 | 3106 / 3106 | 0 | 47 |
| 1 | async (after) | 39 / 39 | 4116 / 4116 | 0 | 79 |
| 5 | sync (before) | 3171 / 3178 | 3171 / 3178 | 0 | 47 |
| 5 | async (after) | 49 / 50 | 3183 / 7250 | 0 | 78 |
| 20 | sync (before) | 33245 / 33246 | 33245 / 33246 | **17 of 20** (`TimeoutError`) | 22 |
| 20 | async (after) | 197 / 240 | 10580 / 16726 | 0 | 97 |

Fake model latency 3 s, local disposable PostgreSQL, single process; run
2026-10-13. The 50-tenant case is covered by the load tests, not timed here.

Reading the table: *accept* is what the visitor waits for before the message is
confirmed and the composer is free; *reply visible* is send → reply on screen.

* **Acceptance** drops from the model time (~3.1 s) to ~40–50 ms, and stays
  under 250 ms at 20 concurrent tenants.
* **Reply visible** is *not* faster for a single visitor: it is the model time
  plus up to one poll interval (≤1 s; the 1-tenant run hit the worst case).
  The win for the visitor is immediate confirmation plus a typing indicator,
  not a shorter model call. Cutting the model time (one model call instead of
  decision + RAG, streaming) is the next lever and is listed as follow-up.
* **Under concurrency the old path fails, the new one queues.** At 20
  simultaneous messages the synchronous path deadlocked on the connection pool
  and 17 of 20 visitors received an error after ~33 s. The cause is
  pre-existing: each sync request holds its session connection and then
  `agent_workflow_service.analyze` checks out a *second* pooled connection
  for its analysis lock (hold-and-wait), so more than about 15 concurrent turns
  exhaust the 15-connection pool. The async path caps running turns at 4 (each
  holds 3 connections), so every message is accepted and answered; the cost is
  queueing: p95 reply 7.3 s at 5 and 16.7 s at 20 simultaneous messages with a
  3 s model. Raising the cap safely requires releasing the DB connection
  during the model call (follow-up), not a bigger number.
* **DB statements per message rise** from 47 to ~80 (job row, claim, the
  visitor's 1 s polls). Each poll is a few indexed single-row queries; at pilot
  volume this is well within Neon free-plan compute, but it is the main
  marginal DB cost of the design and why idle polling backs off to 10–30 s.

Production numbers must be re-measured after rollout with the logged
`public_chat_timing` events and `/observability/usage` p50/p95 — the benchmark
is evidence for the design, not a production SLO.

## 2. Durable asynchronous chat turns (Workstream B)

```
visitor ──POST /messages {delivery:"async"}──▶ API
                                              │ validate, rate-limit, dedupe
                                              │ store inbound + ticket mirror
                                              │ insert IntegrationJob(public_chat.turn)  ← one transaction-safe, idempotent row
◀── 200 {processing_state:"accepted"} ────────┘
                                              │ turn_dispatcher.submit(job)  (after response)
                                              ▼
                     claim_by_id (fenced UPDATE … RETURNING)
                     chat_turn_lease(session)  (advisory lock: one turn per session)
                     allowance check → model → reply → mark_completed (fenced)
visitor ──GET /sessions/updates?after_id=N──▶ messages > N, open turn states, poll_after_ms
```

* **Durability:** the turn is an `integration_jobs` row created before the
  response. Dedupe key `public-chat-turn:{org}:{session}:{client_message_id}`
  makes client retries idempotent.
* **Delivery states:** `accepted` (stored, queued), `processing` (claimed),
  `completed` (reply stored), `failed` → converted to `handoff`, `handoff`
  (human requested / allowance exhausted / permanent failure).
* **No lost turns:** a turn whose in-process task disappears (instance restart)
  is re-dispatched (a) when the visitor polls `/updates` (pending > 3 s, or
  processing with a claim older than `PUBLIC_CHAT_TURN_STALE_SECONDS`), (b) once
  at API start-up, and (c) by the existing scheduled worker as a backstop. All
  three paths go through the same fenced claim, so a turn runs at most once at a
  time and its reply is written exactly once (re-checked under the session
  lease).
* **Failure:** retries follow the job backoff; a terminal failure hands the
  conversation to a human with the standard handoff reply, so a visitor is never
  left waiting on a reply that will not come.
* **Compatibility:** `delivery` defaults to `"sync"`, so the existing contract
  (reply in the response) is unchanged for any client that does not opt in;
  the response gains an optional `processing_state`. The new widget sends
  `delivery:"async"` and falls back to `/sessions/state` polling if
  `/sessions/updates` returns 404 (an older backend). No path version bump is
  needed; `PUBLIC_CHAT_ASYNC_ENABLED=false` forces every request back to sync
  server-side (kill switch).

## 3. Real-time delivery (Workstream C)

| Option | Fit for this stack | Decision |
|---|---|---|
| WebSockets | Vercel functions cannot hold sockets; the browser would need a direct connection to Render (new CORS/CSP surface, bypassing the BFF) and a pub/sub layer for multi-instance fan-out | Rejected for now |
| SSE | Streams through a Vercel function bill for the whole connection lifetime (function duration) and are capped by the function timeout; again needs pub/sub (LISTEN/NOTIFY would pin a Neon connection and keep compute awake) | Deferred |
| **Adaptive cursor polling** | Works through the existing BFF and auth; each poll is one small indexed query; server sets the pace | **Chosen** |

`GET /public/chat/sessions/updates?after_id=` returns only messages after the
cursor plus open turn states and `poll_after_ms` (1000 while a turn is open,
3000 when the poll returned new messages, 10000 when idle). The widget:

* polls 1 s after an accepted send, then follows the hint;
* backs off to ≥30 s in a hidden tab and polls immediately on becoming visible;
* reconnect/missed-event recovery is inherent: the cursor is the last stored
  message id, so after any gap the next poll returns everything missed;
* auth and isolation are unchanged: the session bearer token scopes the query
  to that session's conversation (`test_updates_never_cross_tenants`).

The staff handoff inbox now refreshes itself every 15 s while visible (quiet,
tenant-guarded, read-only), and immediately when the tab regains focus.

SSE remains the upgrade path once a paid always-on API and a pub/sub channel
exist; the cursor endpoint is the resync primitive SSE would also need.

## 4. Multi-tenant commercial readiness (Workstream E)

| Capability | Implementation | Status |
|---|---|---|
| Tenant-fair execution | Global turn semaphore (4) + per-tenant semaphore (2) in `TurnDispatcher`; per-session advisory lease keeps order | Implemented, load-tested |
| Noisy-tenant protection | Per-tenant cap + existing per-session/per-IP rate limits | Implemented, tested (12-session flood) |
| Usage metering & cost attribution | `ai_request_logs` already attribute tokens and estimated cost per tenant; `TenantAIAllowanceService` aggregates month-to-date | Implemented |
| Monthly message/model budgets | `public_chat_configurations.monthly_ai_turn_limit` / `monthly_ai_budget_usd` (migration `1p9a0001`, nullable = platform default 3000 turns / $5) checked **before** any model call; exhausted → human handoff, no model call | Implemented, tested |
| Usage & health reporting | `GET /observability/usage` (OBSERVABILITY_READ, tenant from auth): allowance, 24 h turns completed/failed/open, p50/p95 | Implemented |
| Tenant-specific retrieval | Existing tenant-filtered vector search (Phase 1C.3B) — unchanged | Existing |
| Roles / audit | Existing RBAC capabilities and audit trail — unchanged, new endpoint uses `OBSERVABILITY_READ` | Existing |
| Onboarding | Existing `scripts` onboarding (Phase 1P.3); allowance overrides are two nullable columns set during onboarding | Existing + documented |
| Billing / charging customers | Not built — metering exists; charging requires owner approval | Out of scope |

## 5. Continuous worker viability (Workstream F)

A continuously running worker that polls `integration_jobs` keeps Neon compute
awake 24/7: at the minimum 0.25 CU that is ~182 CU-hours/month, above the free
plan's 100 CU-hours, so it forces the paid Neon plan (≈$19/month at 0.25 CU
always-on) **plus** a Render worker ($7). That breaks the $50 envelope together
with Vercel Pro.

1P.9 therefore does **not** need a continuous worker: interactive turns run in
the API process that accepted them (no polling — event-driven by the request,
the visitor's own poll and start-up), and the existing 5-minute scheduled worker
stays the backstop. A continuous worker becomes worthwhile only when (a) a paid
Neon plan is already justified by load, and (b) it waits on `LISTEN/NOTIFY`
rather than polling. Not enabled; any worker change is owner-gated.

## 6. Security review (Workstream G)

* New public endpoint `/sessions/updates` uses the same session bearer
  verification as `/sessions/state`; queries are bound by session conversation
  **and** organization id.
* Turn processing reloads session/message with the job's organization id; a
  tampered or cross-tenant payload fails permanently without touching the other
  tenant (`test_tampered_turn_payload_cannot_touch_another_tenant`).
* The allowance check runs before any model call and cannot be bypassed by the
  async path (both paths share `_ai_reply_turn`).
* No auth, RBAC, cookie or CSP setting was relaxed. The widget still talks only
  to its own origin (`connect-src 'self'`).
* Logs carry ids, stage timings and error *types* only — no message text, keys
  or tokens.

Residual risks are listed in the runbook.

## 7. Follow-ups (not in this phase)

1. Release the pooled DB connection during the model call
   (`agent_workflow_service.analyze` holds the request session and a second
   lock connection throughout), then raise the turn concurrency cap.
2. Reduce model time: skip the decision call when the grounded-answer path
   applies, or stream the reply; this is the largest remaining share of the
   observed 7 s median.
3. SSE push once an always-on API plus a pub/sub channel are budgeted.
4. Meter embeddings in `ai_request_logs` so cost attribution covers RAG search.
