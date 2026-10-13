# Phase 1P.9 / 1P.9R — Combined production release runbook

Release vehicle: **one combined release PR** (`release/phase1p9-async-public-chat`
→ `master`) containing the complete, verified #15 + #16 implementation.
#15 and #16 stay open as review references only; #17 is CI-only. **None of
them is merged separately.** Evidence:
[architecture/phase1p9r-release-hardening.md](../architecture/phase1p9r-release-hardening.md).

**Nothing in this document has been executed.** Every step marked **OWNER**
is a production change, a paid change, a data write or a model call and needs
explicit owner approval at that moment. Topology is fixed: Vercel
(frontend/BFF), Render (API), Neon (PostgreSQL), Nhost (staff auth). No Replit.

---

## 0. Why one combined release (and never #15 then #16)

Render and Vercel deploy every push to `master` automatically. Merging #15 on
its own would put this code in front of RISPU, for at least one deploy cycle,
before #16 could follow:

* `PUBLIC_CHAT_ASYNC_ENABLED` **defaults to ON** in #15 and there is **no
  per-tenant gate**, while the Vercel deploy of the same merge ships a widget
  that asks for `delivery: "async"` - every tenant, RISPU included, would switch
  to asynchronous turns at once, unreviewed.
* #15 has **no schema gate**. Merged before both migrations, its ORM selects
  columns production does not have and **public chat fails for every visitor**
  until the migration is run.
* #15 carries the defects 1P.9R fixed: messages orphaned by a crash between
  two commits, AI allowance overspend under concurrency, the connection-pool
  deadlock (17/20 visitors failed at 20 simultaneous), burst rate-limiting
  (9 of 10), turns stranded after a restart, advisory-lock leaks.
* Turns accepted by #15 in that window would then be processed by #16 code -
  a mixed state that was never tested.

The combined release moves `master` **once**, from the current production
commit straight to the final hardened code, so only that code is ever
deployed. Use **one** merge of the release PR ("Create a merge commit"). After
it is merged, close #15 and #16 as superseded and close #17.

---

## 1. Budget: $50/month ceiling, no unapproved infrastructure changes

Deploying the release (with async off) needs **no** infrastructure change and
**no** paid change. Activation prerequisites are separate owner decisions.

| Item | Plan | $/month | Status |
|---|---|---|---|
| Vercel | Pro, 1 seat (Hobby is non-commercial) | 20 | **OWNER** decision before paying tenants |
| Render API | Starter 0.5 CPU / 512 MB (certified) | 7 | **OWNER** decision if not already Starter |
| Neon | Free (100 CU-h, 0.5 GB) | 0 | unchanged |
| Nhost | Free | 0 | unchanged |
| GitHub Actions worker | Free | 0 | unchanged |
| OpenAI | pay-as-you-go, hard-capped (§5 A6) | ≤ 10 | capped by the provider hard limit |
| **Maximum** | | **≤ 37** | **≤ $50 ceiling, ≥ $13 headroom** |

Not approved and not part of this release: Render Cron / continuous worker,
Neon paid plan, extra instances, SSE/pub-sub, billing provider. **Do not** run
a 5-minute scheduled worker against Neon Free: anything that touches the
database at least every 5 minutes keeps compute awake (≈ 182 CU-h/month at
0.25 CU) and exhausts the 100 CU-h allowance mid-month, suspending the
database. Check Neon → Usage → compute hours before and after activation.

---

## 2. Database endpoint requirement: direct Neon endpoint only

Reply delivery, chat turns and agent analysis rely on **session-level advisory
locks** held across several transactions on one connection. Neon's `-pooler`
hosts are PgBouncer in **transaction mode**: consecutive transactions may run
on different server connections, so a lock can be taken on one backend and
"released" on another - silently breaking duplicate-send protection and
per-session ordering. **Every production `DATABASE_URL` must use the direct
endpoint** (host without `-pooler`):

* Render API service `DATABASE_URL`;
* the GitHub Actions production worker secret used by `production-worker.yml`;
* the one-off shell used for migrations and the tools below.

Verification (**OWNER**, read-only):

1. `python scripts/validate_production_config.py --environment production`
   reports `DATABASE_URL ... direct (non-pooler) endpoint` - it **fails** on a
   `-pooler` host.
2. Same physical session across transactions:
   `psql "$DIRECT_URL" -c "BEGIN; SELECT pg_backend_pid(); COMMIT;" -c "BEGIN; SELECT pg_backend_pid(); COMMIT;"`
   → both values identical. (Use the libpq form of the URL, without `+asyncpg`.)
3. Record the verified host (not the password) in the release ticket.

---

## 3. Render Starter capacity (certified, fake 2 s model)

One Starter-shaped instance served 50 tenants sending at once, a noisy tenant,
lost-response retries, allowance exhaustion, a 40-turn conversation and a crash
of every in-flight turn with **0 failed requests and 0 ordering, duplicate,
unanswered or cross-tenant violations**; CPU ≤ 0.15 cores, RSS ≤ 240 MB, pool
waits ≤ 0.85 s. Throughput ≈ 6 turns per model-latency; bursts queue (reply p95
≈ 11 s for 20 simultaneous first messages), they do not fail. Do not raise
`PUBLIC_CHAT_TURN_MAX_CONCURRENCY` above 6 without a larger pool and a re-run of
`scripts/certify_public_chat_performance.py`.

---

## 4. AI cost: estimated vs billed

`ai_request_logs.estimated_cost_usd` and the reservations are **estimates**
(token counts × configured prices; embeddings via a fixed per-turn allowance);
`GET /observability/usage` marks them `cost_basis: "estimated"`. **OpenAI
billing is the authority**; reconcile monthly against
`SELECT sum(estimated_cost_usd) FROM ai_request_logs WHERE created_at >= date_trunc('month', now());`.
Per-tenant allowances (pilot: 3000 turns / $5) are set with
`scripts/set_tenant_async_turns.py --turn-limit … --budget-usd …` (dry-run by
default). Exhaustion → human handoff, never a lost message.

---

## 5. Production activation checklist

Stop at every **OWNER** gate. "Expect" lines are the go/no-go checks; any
mismatch → stop and follow §7.

### Phase A — Preflight (before anything changes)

| # | Step | Expect |
|---|---|---|
| A1 | Release PR CI green on the final SHA; PR approved | 9/9 checks green |
| A2 | **OWNER** Neon: create branch `pre-1p9-<date>` from production (snapshot / rollback point) | branch exists |
| A3 | **OWNER** Verify direct endpoints (§2) for Render API, GitHub worker secret and the migration shell | no `-pooler`; same `pg_backend_pid()` |
| A4 | **OWNER** From a one-off shell on the release SHA with production env: `python scripts/validate_production_config.py --environment production` | expected before migration: `ALEMBIC … migration gap: 1p9b0001 not applied`; every other check OK (any other failure → stop) |
| A5 | `python -m alembic current` (same shell) | `1p5a0001` |
| A6 | **OWNER** Render env: set `PUBLIC_CHAT_ASYNC_ENABLED=false` **explicitly** (defense in depth; the code default is already false). Leave `PUBLIC_CHAT_TURN_MAX_CONCURRENCY`, `AGENT_ANALYSIS_MAX_CONCURRENCY`, `PUBLIC_CHAT_TURN_SWEEP_SECONDS` unset (defaults 6 / 8 / 3600); `PLATFORM_MONTHLY_AI_BUDGET_USD=8` | env saved (takes effect with the release deploy) |
| A7 | Read-only baseline: `python -m scripts.check_public_chat_pilot --tenant rispu` | pilot healthy; note queue counts |

### Phase B — Migrations (before any code)

| # | Step | Expect |
|---|---|---|
| B1 | **OWNER** `DATABASE_URL=<direct> scripts/run_migrations.sh` (single run, advisory migration lock) | `1p5a0001 → 1p9a0001 → 1p9b0001` |
| B2 | `python -m alembic current` | `1p9b0001 (head)` |
| B3 | `python scripts/validate_production_config.py --environment production --strict` | `READY` |
| B4 | Every tenant gated off: `SELECT o.external_id, c.id, c.async_turns_enabled FROM public_chat_configurations c JOIN organizations o ON o.id = c.organization_id;` | `async_turns_enabled = false` for **every** row, RISPU included |
| B5 | Old production code still serving (expand-only schema; master's full suite passed on it) | RISPU widget answers as before |

### Phase C — Deploy the combined release (async OFF)

| # | Step | Expect |
|---|---|---|
| C1 | **OWNER** Merge the release PR (one merge commit) → Render + Vercel auto-deploy | one deploy each |
| C2 | Render deploy log | `schema_gate_ok` with `current=['1p9b0001']`; no `schema_gate_refused` |
| C3 | `curl -fsS https://<api>/health`, `…/ready`, `…/version` | 200 / 200 with `checks.database == "ok"` / build identity of the release |
| C4 | Vercel production deployment | region `sin1`; `/chat/embed` loads |
| C5 | **OWNER** (creates one RISPU test session + one AI turn) `python scripts/smoke_public_chat.py --backend-url https://<api> --frontend-url https://<frontend> --widget-key-file <file> --embedding-origin https://rispu.com` | all 8 steps PASS; message answered **synchronously**. Creates one session that ends in a handoff - tell RISPU staff to expect it |
| C6 | **OWNER** Gate proof (same command, `--delivery async`) | step 6 FAILS with "async delivery is not active for this tenant" (overall exit 1 is expected) - proves the gates are closed |
| C7 | `python -m scripts.check_public_chat_pilot --tenant rispu`; Render memory/CPU | healthy; memory < 400 MB |
| C8 | Close #15, #16 (superseded) and #17 (CI-only) | — |

**Stop here.** The release is live with production behaviour unchanged.
Activation is a separate decision.

### Phase D — RISPU-only activation (separate approval)

| # | Step | Expect |
|---|---|---|
| D1 | **OWNER, paid** Render Starter (if not already) and Vercel Pro - within §1 | approved and applied |
| D2 | **OWNER** OpenAI: dedicated production project; Project → Limits: monthly **$10**, "Enforce a hard limit" on, alerts $5/$8; Organization limit ≤ $15 hard | Limits page shows the enforced hard limits |
| D3 | Neon Usage → compute hours this month | on track for < 100 CU-h |
| D4 | **OWNER** Render env `PUBLIC_CHAT_ASYNC_ENABLED=true`, restart | `schema_gate_ok`; C6 still fails (no tenant activated yet) |
| D5 | **OWNER** `python -m scripts.set_tenant_async_turns --organization-id <RISPU id>` (dry run) → check output → same with `--enable --apply` | only RISPU's configuration changes to `true` |
| D6 | B4 query | RISPU `true`, every other tenant `false` |
| D7 | **OWNER** smoke with `--delivery async` | step 6 PASS: "async reply visible after …" |
| D8 | Observe 24–72 h (§6, §8) | acceptance criteria met |

---

## 6. Monitoring and alerts

| Signal | Source | Alert when |
|---|---|---|
| `schema_gate_refused` | Render logs | any |
| `public_chat_turn_failed`, `public_chat_turn_failed_to_handoff` | Render logs | > 2 per hour |
| `public_chat_ai_allowance_exhausted` (`reason=platform_budget_reached`) | Render logs | any |
| `public_chat_turn_startup_recovery_failed`, `public_chat_turn_sweep_failed` | Render logs | any |
| open turns, turn p50/p95, allowance | `GET /observability/usage` (staff) | open > 20 for 10 min; p95 > 30 s |
| CPU / memory | Render metrics | CPU > 80 % of 0.5 for 10 min; memory > 400 MB |
| Neon compute hours | Neon Usage | > 60 CU-h before day 20 |
| OpenAI spend | OpenAI alerts | $5 / $8 |

---

## 7. Drain checks, kill switch and rollback

### Drain check (run before any rollback or deactivation)

```sql
SELECT status, count(*)
FROM integration_jobs
WHERE job_type = 'public_chat.turn'
  AND status IN ('pending', 'retry', 'processing')
GROUP BY status;
```

**Drained = zero rows.** Repeat every 30 s; turns finish within seconds once no
new async messages arrive. Also `GET /observability/usage` →
`chat_turns_24h.open == 0`.

### Emergency kill switch (no deploy)

* **All tenants:** Render env `PUBLIC_CHAT_ASYNC_ENABLED=false`, then restart
  the service. New messages are served synchronously; turns already accepted
  still complete (verified). Recovery and the hourly sweep keep running.
* **One tenant (immediate, no restart):**
  `python -m scripts.set_tenant_async_turns --organization-id <id> --disable --apply`.
* **AI spend:** lower `PLATFORM_MONTHLY_AI_BUDGET_USD` (new turns hand off to
  humans) and/or the OpenAI project hard limit.

### Rollback

1. **Async misbehaving:** kill switch (above) → drain check → done; investigate.
2. **Release code misbehaving:** kill switch → **drain check to zero** →
   Render "Rollback" to the last pre-release deploy and Vercel "Instant
   Rollback" to the previous production deployment → open a revert PR of the
   release merge on `master` (**OWNER**) so the next auto-deploy does not bring
   it back. The schema stays at `1p9b0001` (verified compatible with the
   previous code: master's full suite, 2139 passed).
3. **Rolled back without draining:** the previous code's worker fails queued
   turns as "Unknown job type" without replying (verified). After rolling
   forward again: `python -m scripts.repair_rolled_back_chat_turns` (dry run),
   then `--apply` (**OWNER**) re-queues exactly those turns.
4. **Schema rollback** (last resort, **OWNER**): only with no release code
   running; `alembic downgrade 1p5a0001` (drops only the added objects - tenant
   flags, allowance overrides and reservations are lost), or restore from the
   `pre-1p9-<date>` Neon branch (discards everything written since).

---

## 8. Acceptance criteria (after RISPU activation)

* No `public_chat_turn_failed_to_handoff` caused by system errors in 24 h.
* No accepted message unanswered for > 2 minutes (drain query at quiet times).
* Reply p95 (from `public_chat_timing` logs) within 2× the pre-activation p95.
* Render memory < 400 MB; Neon compute on track for < 100 CU-h.
* Estimated vs billed OpenAI cost within ±30 % after the first week.

## 9. Remaining risks

See the architecture document §5. In addition: single API instance (Render
restarts it; turns recover); backups depend on Neon's restore window - always
branch before migrating; the scheduled worker remains unreliable for non-chat
jobs until the scheduler trial concludes; no customer billing yet.
