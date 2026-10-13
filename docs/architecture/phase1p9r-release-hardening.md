# Phase 1P.9R — Commercial SaaS release hardening

Branch `feat/phase1p9r-release-hardening`, stacked on PR #15
(`feat/phase1p9-saas-foundation`). Nothing here is merged, deployed, migrated
or activated. Operations, budget and rollout: see
[the readiness runbook](../runbooks/phase1p9r-release-readiness.md).

All tests ran against **disposable** PostgreSQL 16 + pgvector containers with
fake model/embedding/RAG adapters. No production database, secret, Zendesk or
OpenAI call was used.

## 1. Confirmed defects and fixes

Each row was reproduced with a test that **fails on the code before the fix
and passes after it** (the "before" column names the code it failed on).

| # | Defect | Before | Fix | Regression test |
|---|---|---|---|---|
| 1 | **Allowance overspend under concurrency.** PR #15 *read* usage then called the model, so simultaneous turns all passed a nearly-exhausted check (a tenant with a 3-turn limit got 4 model calls). | PR #15 | Admission is a *reservation* under one transaction-scoped advisory lock (all instances share the database), idempotent per turn key, with a platform-wide ceiling under the same lock. | `test_phase1p9r_ai_budget.py` (11) |
| 2 | **Orphaned customer message.** The inbound message committed before the turn job; a crash between them left a message nobody would ever answer. | PR #15 | Message + job commit in one transaction; the ticket mirror runs after the commit and is repeated idempotently by the turn. | `test_crash_between_message_and_job_leaves_no_orphaned_message` |
| 3 | **Connection-pool deadlock.** A turn held its session connection while `analyze` checked out a second one for its advisory lock (and async turns a third). 8 concurrent sync turns on a 3-connection pool hit `QueuePool limit … timed out`; 20 simultaneous visitors on the production-shaped pool failed 17/20 in the 1P.9 benchmark. | PR #15 | Read-only session connections are released around model/embedding calls and before taking the lock; a chat turn's analysis lock rides on its lease connection; concurrent analyses are bounded by the pool size. Never commits a transaction holding writes, row locks or xact locks. | `test_phase1p9r_connections.py` (7) |
| 4 | **Rate limiter rejected bursts.** The DB limiter's `INSERT … ON CONFLICT DO NOTHING` rejected every request that lost the race to create a window's bucket: 10 simultaneous visitors on one widget → 1 admitted. **Also on production master (since Phase 1P.2).** | PR #15 and master | One atomic upsert (`ON CONFLICT DO UPDATE … WHERE count < limit`). | `test_phase1p9r_rate_limit_burst.py` |
| 5 | **Dead pooled connections.** After a cancelled request (and whenever Neon scales to zero) the next request received a closed connection and failed. | PR #15 | `pool_pre_ping=True` on the application engine. | `test_cancelled_turn_returns_every_connection_and_lock` |
| 6 | **Unsafe deploy order.** The start preflight never checked the schema; merging PR #15 would have auto-deployed code selecting columns production does not have. Async also defaulted to ON. | PR #15 | Async OFF by default + per-tenant flag; a start-up **schema gate** refuses to serve/consume on an older schema (uvicorn exits before binding; Render keeps the previous deploy). | `test_phase1p9r_rollout_gates.py` (19) |
| 7 | **Stranded turns.** Start-up recovery handled ≤100 turns and skipped a turn whose claim was still fresh at restart (the common deploy-restart case) unless the visitor kept polling. | PR #15 | Paged recovery of every open turn, each scheduled for when its claim becomes stale; start-up recovery retried until the DB answers; hourly in-process sweep. | `test_phase1p9r_recovery.py` (6) |
| 8 | **Advisory lock leak.** The turn lease released only its own key; the analysis lock taken on the same connection (or one granted to a cancelled blocking lock call) went back to the pool still held. | 1P.9R WIP `d71c2ea` | Dedicated lock connections run `pg_advisory_unlock_all()` before returning to the pool, or are invalidated. | `test_phase1p9r_review.py` (lock tests, real QueuePool) |
| 9 | **Polling load under bursts.** Every waiting visitor polled every 1 s; on 0.5 CPU this was the dominant load. | PR #15 | Queued (not yet processing) turns get a backlog-proportional hint (1–5 s); processing turns keep 1 s. | constrained certification |
| 10 | **Rollback strands queued turns.** Verified on master `b50e741`: the old worker fails a queued `public_chat.turn` permanently ("Unknown job type"), no reply, no handoff. | master (old code) | Procedure: kill switch → drain → roll back. Repair tool `scripts/repair_rolled_back_chat_turns.py` (dry-run default) re-queues exactly those turns after rolling forward. | `test_phase1p9r_rollback_repair.py` |
| 11 | **Widget first load carried the site's animation libraries.** The root layout statically imported gsap + lenis, so every tenant page view downloaded them inside the widget iframe. | PR #15 and master | Lazy, route-gated import. | `embed-delivery.test.ts` (source) + measured bundle |

The client also retries ambiguous send failures with the **same**
`client_message_id`, and reuses it if the visitor resends the same text
(`frontend/src/lib/public-chat/send-retry.ts`).

## 2. Architecture after hardening

```
visitor ─POST /messages─▶ rate limit (atomic upsert) ─▶ gate: platform ∧ tenant ∧ delivery=async
        ◀─ 200 accepted ─  INSERT message + INSERT job  (ONE transaction)
                            │ (sync path unchanged when any gate is off)
                            ▼ in-process dispatcher (6 global / 2 per tenant)
                     fenced claim ─▶ release session conn ─▶ lease conn (advisory, per session)
                     ─▶ reserve allowance (advisory xact lock: tenant + platform) ─▶ analysis slot
                     ─▶ analysis lock on the lease conn ─▶ model (no session conn held)
                     ─▶ reply (deduped) ─▶ settle reservation ─▶ fenced complete
recovery: visitor poll · start-up (all, paged, retried) · hourly sweep · scheduled worker
```

Connections pinned during a model call: **one per running turn** (its lease
connection). Analysis slots = `min(8, pool − turns − 2)`.

## 3. Performance certification (Workstream F)

`scripts/certify_public_chat_performance.py`: the real app under uvicorn in
`docker --cpus 0.5 --memory 512m` (Render Starter shape), production pool
(5 + 10, pre-ping); the load generator runs **outside** the container over real
HTTP; fake decision model 2.0 s, fake retrieval 0.3 s. These numbers show
queueing, fairness, safety and resource use on this machine. They are **not**
production latency: production adds browser → Vercel → Render → Neon round
trips and real, variable model time.

### 3.1 Turn-concurrency sweep (20 tenants at once, constrained)

| Turn concurrency | Analysis slots | Queue wait p95 | Reply visible p95 | Pool peak | Pool wait p95 / max | CPU (cores) | Failures |
|---|---|---|---|---|---|---|---|
| 2 | 8 | 22.2 s | 25.3 s | 15 | 110 / 307 ms | 0.071 | 0 |
| 4 (PR #15 default) | 8 | 10.5 s | 13.9 s | 15 | 76 / 317 ms | 0.090 | 0 |
| **6 (new default)** | 7 | **8.0 s** | **10.8 s** | 15 | 89 / 336 ms | 0.135 | 0 |
| 8 | 5 | 7.6 s | 10.7 s | 15 | 80 / 283 ms | 0.121 | 0 |

8 is not faster: the analysis slots (15 − 8 − 2 = 5) become the limit and the
decision stage itself queues (p95 4.75 s vs 2.5 s). The default is raised to 6
on this evidence; higher values need a larger pool *and* a re-run.

### 3.2 Workloads (final defaults, constrained)

| Workload | Msgs | Failed | Ack p50 / p95 | Queue wait p95 | AI decision p95 | RAG p95 | Reply visible p50 / p95 / max | DB stmts/msg | Pool peak / wait max | CPU (cores) | RSS MB | Violations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 tenants two messages each | 2 | 0 | 32 ms / 32 ms | 48 ms | 2.3 s | 301 ms | 3.1 s / 3.1 s / 3.1 s | 79.5 | 2 / 14 ms | 0.024 | 220.7 | none |
| 5 tenants two messages each | 10 | 0 | 72 ms / 150 ms | 198 ms | 2.4 s | 304 ms | 3.2 s / 3.3 s / 3.3 s | 72.9 | 10 / 67 ms | 0.093 | 223.5 | none |
| 20 tenants two messages each | 40 | 0 | 308 ms / 593 ms | 7.2 s | 2.4 s | 310 ms | 7.1 s / 10.7 s / 10.7 s | 71.6 | 15 / 424 ms | 0.129 | 227.4 | none |
| 50 tenants two messages each | 100 | 0 | 261 ms / 1.3 s | 17.8 s | 2.6 s | 309 ms | 16.3 s / 21.8 s / 22.8 s | 73.3 | 15 / 848 ms | 0.132 | 232.8 | none |
| noisy tenant 20 sessions plus 10 quiet | 30 | 0 | 834 ms / 1.1 s | 24.8 s | 2.5 s | 305 ms | 11.9 s / 28.3 s / 28.3 s | 87.9 | 15 / 526 ms | 0.073 | 233.8 | none |
| one tenant 10 concurrent with 2 lost response retries | 10 | 0 | 378 ms / 586 ms | 9.9 s | 2.4 s | 307 ms | 7.6 s / 12.8 s / 12.8 s | 105.6 | 14 / 78 ms | 0.061 | 234.0 | none |
| allowance exhaustion 10 tenants limit 1 turn | 30 | 0 | 59 ms / 388 ms | 2.8 s | 2.5 s | 303 ms | 2.2 s / 5.4 s / 5.4 s | 47.0 | 12 / 90 ms | 0.149 | 234.4 | none |
| long conversation 40 turns | 40 | 0 | 20 ms / 25 ms | 28 ms | 2.4 s | 304 ms | 3.1 s / 3.1 s / 3.1 s | 78.1 | 2 / 0 ms | 0.019 | 235.2 | none |
| crash all inflight turns 20 tenants | 20 | 0 | 522 ms / 614 ms | 8.5 s | 2.5 s | 311 ms | 6.8 s / 11.9 s / 11.9 s | 92.3 | 15 / 297 ms | 0.13 | 235.5 | none |
| sweep turn concurrency 6 20 tenants | 20 | 0 | 547 ms / 654 ms | 7.6 s | 2.5 s | 314 ms | 5.8 s / 10.8 s / 10.8 s | 80.7 | 15 / 324 ms | 0.123 | 235.4 | none |

Noisy tenant fairness: the flooding tenant's 20 visitors waited p95 28.3 s (capped at 2 concurrent turns), while 10 quiet tenants arriving at the same time got replies at p95 7.0 s.
Allowance exhaustion: 10 tenants with a 1-turn limit sent 3 messages each → exactly 10 model calls; the rest handed off.
Crash: all 20 in-flight turns cancelled → 20 recovered on "restart" → 20 completed, 20 model calls, 0 duplicate replies.
Staff visibility is MODELLED (handoff stored + uniform 0–15 s inbox refresh): p95 30.8 s at 50 tenants.
`docker stats` peaks over the run: CPU 36.7 % of one core, memory 210.7 MiB of 512 MiB. Raw results: `docs/performance/phase1p9r-constrained-final.json` (final defaults) and `docs/performance/phase1p9r-constrained-sweep.json` (first run incl. the 2/4/6/8 sweep).

### 3.3 First website load (widget)

Measured with `next start` on this machine (no CDN, no network RTT): the
embed loader script is 2.0 KB (TTFB p50 0.7 ms) and the `/chat/embed` HTML
14.8 KB (TTFB p50 2.9 ms). The widget's first-load JavaScript was
**701 KiB raw / 223 KiB gzip**, of which 129 KiB raw / 48 KiB gzip was the
marketing site's smooth-scrolling (gsap + lenis) pulled in by the root layout.
Finding 11: it is now loaded lazily and never on `/chat/embed`
(`components/smooth-scroll-gate.tsx`) → **576 KiB raw / 177 KiB gzip (−21 %)**,
and gsap/lenis are out of every page's initial load. On Vercel the transfer is
compressed and cached at the edge; real first-load time depends on the
visitor's network and is not claimed here.

### 3.4 Connection pool and memory findings

* **Pool 15/15 is reached by request bursts, not leaked connections.** Every
  workload with ≥15 concurrent visitors touches the 5 + 10 ceiling for a
  moment; requests then wait for a connection (p95 ≤ 170 ms, max 0.7 s at 50
  simultaneous tenants) - nowhere near the 30 s pool timeout, and with zero
  pool-timeout failures. The pre-hardening code deadlocked at the same load.
* **Memory:** the API process stays at 220–240 MB RSS under every workload
  (container ≤ 211 MiB by `docker stats`) - under half of 512 MB, no OOM, no
  growth across the long conversation (40 turns).
* **CPU:** ≤ 0.149 cores averaged per workload (≤ 30 % of the 0.5-CPU
  allowance); `docker stats` instantaneous peak 36.7 % (first run 48 %). Throughput is bounded by
  model time × turn concurrency, not by CPU.
* **Keep-alive:** the only failures in the first run were 5 polls
  (`RemoteProtocolError`) caused by uvicorn's default 5 s keep-alive racing the
  client's connection reuse. Production runs uvicorn with 30 s
  (`scripts/start_render_api.sh`); the final run uses 30 s.

### 3.5 Before / after

| Scenario | PR #15 (1P.9) | 1P.9R |
|---|---|---|
| 20 simultaneous visitors, sync path (production today) | 17/20 failed (pool deadlock) | 0 failed |
| 8 concurrent sync turns, 3-connection pool | `QueuePool … timed out` | 8/8 answered |
| 10 simultaneous visitors on one widget | 1 admitted, 9 × "busy" | 10 admitted |
| Tenant with 3-turn limit, 10 queued turns | 4 model calls | 3 model calls |
| Crash between message and job | orphaned message | nothing stored; retry succeeds |
| Turn mid-model-call when the process restarts, visitor gone | waits for a visitor poll / worker | completes after the claim goes stale |
| Async default | ON | OFF (platform + tenant gates) |
| Code deployed before its migration | serves, then fails queries | refuses to start; old deploy keeps serving |

## 4. Cross-version compatibility (Workstream 2)

Disposable PostgreSQL (`cxops_compat_disposable_test`), master `b50e741` in a
detached worktree, this branch's code and image.

| Check | Result |
|---|---|
| Production master's **full** backend suite on the expanded `1p9b0001` schema | **2139 passed, 0 failed** |
| Master's public-chat contract suite (old widget expectations) on the **new backend**, defaults | **65 passed** (only change: new table added to the copy's teardown purge list) |
| **New widget → old backend**: POST with `delivery:"async"` | 200 with the reply (unknown field ignored); `/sessions/updates` → **404** → widget falls back to `/sessions/state` (client tests cover the fallback) |
| Async disabled preserves production behaviour | gate matrix: every combination except platform ∧ tenant ∧ async returns the synchronous reply and creates no job |
| RISPU-only activation | `test_activating_one_tenant_never_enables_async_for_another` |
| Restart with queued and processing turns | recovery tests + constrained crash workload (20 cancelled → 20 recovered → 20 completed, 0 duplicates) |
| **Rollback** (old worker meets a queued turn) | **hazard confirmed**: job failed "Unknown job type", no reply, session left `ai_active` → kill-switch-and-drain procedure + repair tool |
| Schema gate, real process | image under `ENVIRONMENT=production` against a DB at `1p9a0001`: `schema_gate_refused` → "Application startup failed. Exiting." → port never answered; positive control at `1p9b0001`: HTTP 200 |
| Kill switch with accepted turns | `test_kill_switch_stops_new_async_but_queued_turns_finish` |
| Migration `1p9b0001` | upgrade → downgrade → upgrade on disposable DB; head/current checks |

## 5. Reliability and security review (Workstream 3)

| Area | Verdict | Evidence |
|---|---|---|
| Cross-tenant leakage | none found | session-bound queries; reservations keyed by tenant; every certification session checked for foreign-tenant messages (0) |
| Duplicate visitor messages | none | atomic accept + dedupe keys; lost-response retries in certification (0 duplicates) |
| Duplicate AI execution | bounded | fenced claims + per-session lease; a crash mid-call may repeat that one model call (first run: 21 calls for 20 crashed turns; final run: 20), never the reply; reservation reused |
| Lost accepted messages | none found | atomic accept, recovery paths, crash workload |
| Concurrent reservations / platform ceiling | enforced | cross-instance tests incl. 50 tenants and the platform ceiling |
| Pool exhaustion / cancellation | fixed | findings 3, 5, 8 |
| Advisory-lock correctness | fixed | finding 8 |
| Stale job recovery | fixed | finding 7 |
| Rate-limit correctness | fixed | finding 4 (per-session limit still enforced: `test_per_session_rate_limit_is_still_enforced`) |
| Auth / RBAC | no change | `/observability/usage` still 401 without staff auth; no auth code changed |
| Expired sessions | safe | queued turn of an expired session completes without a model call; token refused |
| Worker ↔ API concurrency | safe | scheduled worker racing the dispatcher answers each turn once |
| Model failure → handoff | async: handoff after retries; sync: unchanged legacy behaviour (see residual risks) | `test_permanent_turn_failure_hands_off_instead_of_hanging` |

### Residual risks (not fixed here)

1. **Sync path model failure** (production behaviour today, kept when async is
   off): the request fails, the message stays without reply; a retry returns
   409 "in flight". Async mode hands off instead. Activating async for a tenant
   removes this for that tenant.
2. **Staff-triggered AI** (staff analysis, RAG answers, evaluations) is
   metered and counted against the platform ceiling for *public-chat
   admission*, but is not itself capped.
3. **Estimated cost ≠ billed cost.** Token-price estimates; embeddings are
   covered by a fixed per-turn allowance. Provider hard limits are the
   authority (runbook §4).
4. **One reservation lock for the platform** serialises admissions (a few
   short queries each). Fine at pilot scale; becomes a contention point at
   hundreds of turns per second.
5. **The scheduled backstop is unreliable** (GitHub ran 1.5 % of `*/5` windows).
   Recovery no longer depends on it, but other job types still do.
6. **Rollback strands queued turns** unless the drain procedure is followed;
   the repair tool needs owner approval to run against production.
7. **Fake-model certification**: real OpenAI latency/variance and network
   round trips are not represented; re-measure on staging after activation.
