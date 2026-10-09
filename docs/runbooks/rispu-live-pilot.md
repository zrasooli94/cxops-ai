# Rispu live pilot runbook (public chat)

Operational procedure for the Phase 1P.7 Rispu public-chat live pilot. This is
the monitoring and incident surface for the customer-facing widget piloted at
rispu.com.

Every command in this runbook is **read-only**. The live-pilot dashboard and the
`check_public_chat_pilot` CLI only run SELECT aggregates; neither endpoint
writes, approves, or executes anything. If a runbook step ever looks like it
needs a write, stop and open an incident review instead.

---

## 1. Topology

| Component | Where | Notes |
| --- | --- | --- |
| Widget / embed | Vercel frontend | Serves the control-center dashboard and the embed script. |
| Public chat API | Render (API process) | Auth-free widget routes under `/public/chat/*`. |
| Staff API | Render (API process) | Capability-gated staff routes under `/staff/*`, incl. `GET /staff/public-chat/summary`. |
| Database | Neon PostgreSQL | Only production database. Every summary query is tenant-scoped in SQL. |
| Background jobs | Render (worker process) | Not used by the summary surface; the summary reads durable rows only. |

There is **no** separate metrics database. The dashboard and CLI surface the
tenant's existing durable rows (`public_chat_sessions`,
`ai_request_logs`, `agent_action_events`, `business_actions`,
`integration_jobs`, `business_actions`). If a metric is missing, the source
table is missing it — the fix is to record the event, not to invent a number.

---

## 2. Normal behavior

- The dashboard is at the control-center `/public-chat` page, "Live Pilot
  Operations" section. Select `24h` or `7d`; the page refreshes manually.
- Safe signals (expect these to be non-zero after real traffic):
  `sessions_created`, `customer_messages`, `grounded_public_auto_replies`.
- Don't-expect signals (must stay at zero):
  `safety.public_chat_integration_jobs`,
  `safety.autonomous_public_chat_executions`.
- `queue.human_requested` / `queue.human_assigned` + `queue.ai_active` count
  only **live** sessions; expired sessions are excluded (they are nobody's
  pending work). `queue_health` measures only sessions currently waiting for
  their first staff assignment (`human_requested` and live): `oldest_waiting_*`
  means "waiting since the latest entry into `human_requested`", an assigned
  session is being handled and never trips attention. It turns "attention" only
  when the oldest live waiting entry has been waiting at least 15 minutes. That
  threshold is an **operator-attention signal, not an SLA**.
- RAG metrics count only rows recorded with `feature='public_chat'`. Rows
  written before Phase 1P.7 carry the shared `rag_answer` label, so historical
  `rag_requests`/`rag_errors`/costs are a **lower bound**, by design.
  `avg_rag_latency_ms` averages **successful rows only** — the synthetic
  failure marker rows carry `latency_ms=0.0` and would distort the figure. AI
  cost is a lower bound too, because failed requests record no token usage and
  cannot be costed exactly.

Quick read-only check (run from the repo against the production API
environment):

```bash
.venv/bin/python -m scripts.check_public_chat_pilot --tenant rispu
```

---

## 3. Health checks

| Signal | How | Meaning |
| --- | --- | --- |
| Liveness | `GET /health` | Process is serving. Never touches PostgreSQL. |
| Readiness | `GET /ready` | PostgreSQL answers `SELECT 1`. 503 means "not in rotation". |
| Config gate | `scripts/validate_production_config.py` | Secrets, URLs, auth, migration head. |
| Pilot health | `scripts/check_public_chat_pilot.py --tenant rispu` | Verdict `READY` / `ATTENTION` with bounded reasons. |
| Dashboard | control-center `/public-chat` | Same aggregates the CLI reads, through the API. |

`READY` means: widget enabled, queue healthy, no RAG failures, and no
integration/autonomous activity attributable to public chat. Anything else is
`ATTENTION` with the specific warning printed.

A green `/health` with a red `/ready` is a database problem, not a dead
process. Restarting Render instances will not fix readiness; check Neon first.

---

## 4. Safety expectations

The pilot is deliberately **human-in-the-loop and integration-free**:

- Grounded auto-replies are allowed; that is the pilot's purpose. The summary
  tells you how many happened.
- The safety card warns whenever `integration_jobs` or `autonomous
  executions` attributable to public chat is non-zero — because for this
  pilot they should **never** be non-zero.
- Attribution is exact. An integration job counts only when it is an
  agent-execution job (`job_type = 'agent.execute'`) whose payload `run_id`
  resolves to a tenant-scoped agent run on a ticket that owns a public-chat
  session in the Rispu tenant. An autonomous execution is an agent run
  authorized by policy (`authorization_source = 'policy_auto'`) that attempted
  execution (`executed` / `execution_failed`) on a public-chat ticket.
  Human-approved runs are deliberately **not** counted as autonomous even
  though they carry a run id.
- Never rotate the widget key, never change the grounded-reply eligibility
  rules, and never enable an autonomous execution for public chat without a
  new phase and review.

---

## 5. Incident actions

1. **Queue stuck > 15 minutes** (`queue_health` = attention): assign or release
   sessions from the handoff queue below the pilot section. `oldest_waiting_*`
   points at the longest-waiting entry by `updated_at`.
2. **RAG errors climb**: `rag.rag_errors` counts failed grounded-reply
   attempts. The marker row the widget wrote is bounded (feature, status, a
   fixed placeholder question that never contains the customer's text, no
   model output); investigate retrieval/model service health, do not
   auto-deny.
3. **Safety card red**: a summary that counts integration jobs or autonomous
   executions is the dangerous state. Stop, read the rows the CLI warning
   names, and confirm each is expected before any further action. There is no
   "just re-run" path here.
4. **Widget disabled or blank**: check the frontend embed, the org config
   (`widget_enabled`), and Vercel logs. The summary itself cannot re-enable.
5. **Dashboard/API down**: follow the production topology checklist
   (Vercel + Render + Neon). `/health` and `/ready` localize the component.

---

## 6. Deployment verification

After any production deployment:

1. Run `scripts/validate_production_config.py` (must pass, or the process must
   not bind).
2. Hit `GET /health` and `GET /ready` on the deployed API.
3. Open the control-center `/public-chat` page and confirm the pilot section
   loads with the production tenant id and both windows.
4. Run `scripts/check_public_chat_pilot.py --tenant rispu`; expect `READY` (or
   a named warning you can explain).
5. Confirm the handoff queue below the pilot section still lists and
   assigns/releases sessions.

Nothing about the summary changes the widget: the embed contract and
handoff behavior are untouched by this runbook.

---

## 7. Live smoke checklist

Run against the production API before declaring the pilot healthy:

```bash
.venv/bin/python scripts/validate_production_config.py
.venv/bin/python scripts/smoke_public_chat.py \
    --backend-url <render-api> \
    --frontend-url <vercel-frontend> \
    --widget-key-file <key-file> \
    --embedding-origin https://www.rispu.com
.venv/bin/python -m scripts.check_public_chat_pilot --tenant rispu
```

After the smoke run completes, the pilot summary should show exactly one new
`session_created` and one `session_closed` (the smoke session was closed and its
rows are durable). The smoke transcript never prints the widget key, the session
token, or message bodies.