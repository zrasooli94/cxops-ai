# Phase 1P.9R — Release readiness, budget and rollout

Applies to PR #15 (`feat/phase1p9-saas-foundation`) + the 1P.9R hardening PR
stacked on it. Evidence: [architecture/phase1p9r-release-hardening.md](../architecture/phase1p9r-release-hardening.md).

**Nothing in this document has been executed.** Every step marked **OWNER**
is a production change, paid change or data write that needs explicit owner
approval. Topology is fixed: Vercel (frontend/BFF), Render (API), Neon
(PostgreSQL), Nhost (staff auth). No Replit.

## 1. Monthly budget ($50 ceiling until the first paying customer)

Prices as published October 2026; confirm in each dashboard before approving.

| Item | Plan | $/month | Notes |
|---|---|---|---|
| Vercel | **Pro, 1 seat** | 20 | Hobby is non-commercial; Pro needed before paying tenants. Pin functions to `sin1` (`frontend/vercel.json`). |
| Render API | **Starter** (0.5 CPU, 512 MB) | 7 | Certified in §3; Free instances sleep after 15 min (cold starts on a visitor's first message). |
| Neon | Free (100 CU-h/project/month, 0.5 GB) | 0 | See §2 - the compute-hour allowance is the main risk. |
| Nhost | Free | 0 | Staff auth only; unchanged. |
| GitHub Actions worker | Free (public repo) | 0 | Unreliable cadence (1.5 % of windows); no longer relied on for chat. |
| OpenAI | Pay-as-you-go | ≈ 2–10 | ≈ $0.00096 per AI turn with RAG (estimate); bounded by allowances and the provider hard limit (§4). |
| **Total** | | **≈ 29–37** | ≥ $13 headroom |

Not in the $50 envelope (later, each an owner decision): Render Cron for the
worker (+≥ $1, and see §2), Neon Launch (usage-based, ≈ $19 at 0.25 CU
always-on), a second API instance, SSE/pub-sub, a billing provider.

## 2. Hosting requirements

| Service | Requirement |
|---|---|
| Render API | Starter or larger; region Singapore (same as Neon); start command `scripts/start_render_api.sh` (env preflight, then uvicorn with `--timeout-keep-alive 30`); health check path `/ready`; **one** instance (the dispatcher is per process; more instances are safe but each has its own concurrency budget). |
| Render env | `PUBLIC_CHAT_ASYNC_ENABLED` **unset/false** until §6 step 7; `PLATFORM_MONTHLY_AI_BUDGET_USD` (default 15) ≤ the OpenAI hard limit; defaults for `PUBLIC_CHAT_TURN_MAX_CONCURRENCY` (6), `AGENT_ANALYSIS_MAX_CONCURRENCY` (8), `PUBLIC_CHAT_TURN_SWEEP_SECONDS` (3600). |
| Vercel | Pro; `regions: ["sin1"]`; `BACKEND_API_URL` → Render API. |
| Neon | Singapore; the API may use the pooled or direct endpoint; the app pool is 5 + 10 with pre-ping. |
| Nhost | unchanged. |

### Neon Free compute risk

* Free = 100 CU-hours per project per month; compute suspends after 5 idle
  minutes and **cannot** be kept warm on Free. At the minimum 0.25 CU that is
  ≈ 400 active hours (≈ 55 % of a month).
* Anything that touches the database at least every 5 minutes keeps compute
  awake permanently (≈ 182 CU-h/month at 0.25 CU) and **exhausts the free
  allowance mid-month - compute is then suspended until the next cycle (an
  outage).** The proposed 5-minute Render Cron worker
  (`docs/runbooks/phase1p9-scheduler-trial.md`) would do exactly that; adopt it
  only with a 15–30 minute cadence or together with Neon Launch.
* Phase 1P.9R adds at most one sweep query per hour per API instance and no
  continuous polling. Visitor polling only happens while a visitor waits.
* Every first query after a suspension pays a resume (≈ 0.3–1 s); the pool's
  pre-ping replaces connections Neon dropped while suspended.
* **OWNER check before activation:** Neon console → Usage → compute hours
  for the current month; alert at 60 CU-h.

## 3. Render Starter capacity (certified, fake model 2 s)

* One Starter instance served 50 tenants sending at once (100 messages) and a
  40-turn conversation with 0 failed sends, 0 lost/duplicated/mis-ordered or
  cross-tenant messages, CPU ≤ 0.135 cores sampled (≤ 27 % of 0.5), RSS ≤ 240
  MB (< 50 % of 512 MB), no OOM.
* Throughput ≈ `turn concurrency / model seconds` = 6 / (2–3 s) ≈ 2–3 turns/s
  (≈ 7,000–10,000 turns/hour) - far above pilot volume. Simultaneous bursts
  queue (reply p95 ≈ 11 s for 20 simultaneous first messages with a 2 s
  model); they do not fail.
* The pool reaches 15/15 during bursts with sub-second waits; do not raise
  `PUBLIC_CHAT_TURN_MAX_CONCURRENCY` above 6 without raising the pool and
  re-running `scripts/certify_public_chat_performance.py`.

## 4. AI cost: estimated vs billed, and provider-side controls

* `ai_request_logs.estimated_cost_usd` and the reservations are **estimates**
  (token counts × configured list prices; embeddings via a fixed per-turn
  allowance). `GET /observability/usage` labels them `cost_basis: "estimated"`.
* **OpenAI billing is the authority.** Reconcile monthly: OpenAI Usage page
  (per project) vs `SELECT sum(estimated_cost_usd) FROM ai_request_logs WHERE
  created_at >= date_trunc('month', now())`.
* **OWNER - configure provider hard limits** (OpenAI docs, "Spend limits"):
  1. Create a dedicated OpenAI **project** for CXOps production and use its
     key only in Render.
  2. Project settings → Limits → monthly spend limit **$10**, enable
     **"Enforce a hard limit"**; spend alerts at $5 and $8.
  3. Organization limits → monthly limit ≤ $15 with hard limit enforced.
  4. Verify: the Limits page shows "hard limit enforced"; when reached, API
     calls return 429 (`…spend_limit_exceeded`). For async tenants the turn
     is retried, then handed to a human - the message is never lost; the
     legacy synchronous path returns an error to the widget instead.
  5. Keep `PLATFORM_MONTHLY_AI_BUDGET_USD` **below** the OpenAI project hard
     limit (e.g. $8 vs $10) so CXOps refuses new turns first; provider
     enforcement can lag slightly.

### Per-tenant subscription allowances

| Plan (proposal) | `monthly_ai_turn_limit` | `monthly_ai_budget_usd` |
|---|---|---|
| Pilot (RISPU) | 3000 | 5.00 |
| Starter | 1500 | 2.50 |
| Growth | 6000 | 10.00 |

Set with `scripts/set_tenant_async_turns.py --turn-limit … --budget-usd …`
(dry-run by default). Exhaustion → human handoff, never a lost message.
Charging customers is out of scope until approved.

## 5. Production migration sequence (migration BEFORE code)

Both migrations are expand-only and verified compatible with production master
(full master suite on the expanded schema: 2139 passed).

1. **OWNER** Neon: create a branch `pre-1p9b-<date>` from production (instant
   copy-on-write snapshot; the rollback point). Note the plan's restore window.
2. **OWNER** Confirm `alembic current` = `1p5a0001` with
   `python scripts/validate_production_config.py` (read-only).
3. **OWNER** Run `scripts/run_migrations.sh` once from a one-off shell with the
   production `DATABASE_URL` → `1p9a0001` → `1p9b0001`. (Adds nullable
   columns, `async_turns_enabled` NOT NULL DEFAULT false, the reservations
   table and one index on `ai_request_logs(created_at)` - a brief write lock on
   a small table.)
4. Verify `alembic current` = `1p9b0001`; production (old code) keeps
   serving - nothing reads the new objects yet.
5. Only now merge (§6). If code is merged first, the schema gate makes the new
   deploy fail its start-up and Render keeps the old deploy serving.

Downgrade (only if no new-code deploy is live): `alembic downgrade 1p5a0001`
drops only the added objects; async flags and reservations are lost.

## 6. Staged deployment checklist

| # | Step | Owner approval | Go / no-go check |
|---|---|---|---|
| 1 | Review PR #15 and the 1P.9R PR; CI green on both | yes | all checks green |
| 2 | Neon branch snapshot + migrations (§5) | **yes** | `alembic current` = `1p9b0001` |
| 3 | Merge PR #15, then the 1P.9R PR (in that order) → Render/Vercel auto-deploy with async **off** | **yes** | Render log `schema_gate_ok`; `/ready` 200; RISPU widget unchanged (sync replies) |
| 4 | Render Free → Starter if not already | **yes, paid** | no cold start on first message |
| 5 | Vercel Hobby → Pro; confirm `sin1` | **yes, paid** | function region `sin1` in deployment details |
| 6 | OpenAI project + hard limits (§4) | **yes** | limits page shows enforced hard limit |
| 7 | Set `PUBLIC_CHAT_ASYNC_ENABLED=true` (platform gate) - still no tenant activated | **yes** | behaviour unchanged (all tenants still sync) |
| 8 | RISPU only: `python -m scripts.set_tenant_async_turns --organization-id <RISPU> --enable` (dry run), then `--apply` | **yes** | other tenants' `async_turns_enabled` = false |
| 9 | Observe RISPU 24–72 h (§7) | - | acceptance criteria §9 |
| 10 | Further tenants one at a time | **yes each** | same |

## 7. Monitoring and alerts

| Signal | Source | Alert when |
|---|---|---|
| `schema_gate_refused` | Render logs | any occurrence (deploy blocked) |
| `public_chat_turn_failed` / `public_chat_turn_failed_to_handoff` | Render logs | > 2 per hour |
| `public_chat_ai_allowance_exhausted` (reason `platform_budget_reached`) | Render logs | any (platform ceiling hit) |
| `public_chat_turn_startup_recovery_failed`, `public_chat_turn_sweep_failed` | Render logs | any |
| Open turns, p50/p95 turn latency, allowance | `GET /observability/usage` (staff) | open turns > 20 for 10 min; p95 > 30 s |
| Render CPU / memory | Render metrics | CPU > 80 % of 0.5 for 10 min; memory > 400 MB |
| Neon compute hours | Neon console | > 60 CU-h before day 20 |
| OpenAI spend | OpenAI limits/alerts | alert thresholds in §4 |
| Scheduled worker dead-man | existing audit script | as per the scheduler runbook |

## 8. Rollback and emergency kill switch

* **Kill switch (seconds, no deploy):** set `PUBLIC_CHAT_ASYNC_ENABLED=false`
  in Render (restart applies it). New messages are served synchronously again;
  turns already accepted still complete (verified). Per tenant:
  `set_tenant_async_turns --disable --apply`.
* **Code rollback:** (1) kill switch; (2) wait until open turns = 0
  (`/observability/usage` → `chat_turns_24h.open`, or
  `SELECT count(*) FROM integration_jobs WHERE job_type='public_chat.turn' AND
  status IN ('pending','retry','processing')`); (3) redeploy the previous
  release. The schema stays at `1p9b0001` (compatible).
* **If rolled back without draining**, the old worker fails queued turns
  ("Unknown job type") without replying. After rolling forward again, run
  `python -m scripts.repair_rolled_back_chat_turns` (dry run), then
  `--apply` (**OWNER**) - it re-queues exactly those turns.
* **AI spend emergency:** lower `PLATFORM_MONTHLY_AI_BUDGET_USD` (new turns
  hand off) and/or the OpenAI hard limit.
* **Schema rollback** only from the Neon snapshot branch, and only with owner
  approval (it discards data written since).

## 9. Release acceptance criteria (RISPU activation)

* CI green on both PRs; this document's checks pass at each step.
* 24 h after RISPU activation: 0 `public_chat_turn_failed_to_handoff` caused
  by system errors; no unanswered `ai_active` message older than 2 min
  (`/observability/usage` open = 0 at quiet times); reply p95 within 2× the
  pre-activation p95 measured from `public_chat_timing` logs; Render memory
  < 400 MB; Neon compute on track (< 100 CU-h for the month).
* Estimated vs billed OpenAI cost within ±30 % after the first week.

## 10. Remaining SaaS-readiness risks

See the architecture document §5 (residual risks). In addition: one API
instance is a single point of failure (Render restarts it; turns recover);
backups depend on Neon's plan restore window - take a branch snapshot before
every migration; the scheduled worker remains unreliable for non-chat jobs
until the scheduler trial concludes; customer billing does not exist yet.
