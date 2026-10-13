# Phase 1P.9 — $50/month budget and staged rollout

> **Superseded by [phase1p9r-release-readiness.md](phase1p9r-release-readiness.md)**
> (Phase 1P.9R): async now defaults OFF with per-tenant activation, the
> migration must precede the code, turn concurrency is 6, and the rollback
> procedure changed. Kept for history.

Every step below that deploys, changes a setting, upgrades a plan or touches
production data is an **owner approval gate**. Nothing here has been executed.

## 1. Monthly budget (USD, ceiling $50)

Prices checked October 2026 from vendor pages and recent summaries; confirm on
each vendor's pricing page before approving a paid change.

| Item | Plan | $/month | Why |
|---|---|---|---|
| Vercel (frontend, BFF, widget) | **Pro, 1 seat** | 20 | Hobby is restricted to non-commercial use; a paying-tenant SaaS needs Pro. Includes usage credit that covers pilot traffic |
| Render API (`cxops-api`) | Starter 512 MB / 0.5 CPU | 7 | Always-on: no 15-minute spin-down cold start on a visitor's first message. Runs the in-process turn dispatcher |
| Neon PostgreSQL | Free (100 CU-h/project, 0.5 GB) | 0 | Fits while compute scales to zero between bursts; no always-on poller is added |
| Nhost (staff auth) | Free | 0 | Unchanged |
| GitHub Actions scheduled worker | Free minutes | 0 | Unchanged backstop (5-minute one-shot) |
| OpenAI (gpt-4o-mini decision + RAG; embeddings) | Pay-as-you-go | ≈ 5–12 | ≈ $0.0006–0.0013 per AI turn at $0.15/$0.60 per M tokens; hard-capped per tenant by the allowance |
| **Total** | | **≈ 32–39** | **≥ $11 headroom** |

Per-tenant AI allowance default: 3000 turns or $5.00 per month, whichever comes
first (`PUBLIC_CHAT_DEFAULT_MONTHLY_AI_TURNS`,
`PUBLIC_CHAT_DEFAULT_MONTHLY_AI_BUDGET_USD`, per-tenant overrides on
`public_chat_configurations`). Platform model spend is therefore bounded by
`tenants × $5`; with the OpenAI project hard limit set to the budget line above,
a tenant that runs out is handed to humans instead of incurring spend.

### What does NOT fit in $50 (later)

| Item | +$/month | Trigger |
|---|---|---|
| Continuous Render worker | +7 | Only with LISTEN/NOTIFY and when interactive volume outgrows the API instance |
| Neon paid (always-on or > 100 CU-h, > 0.5 GB) | ≈ +19 at 0.25 CU always-on | Storage > 0.5 GB or sustained traffic keeping compute awake |
| SSE/WebSocket push + pub/sub (e.g. managed Redis) | +10–30 | When 1 s polling is no longer acceptable |
| Second API instance / larger Render plan | +7–25 | When the 4-turn global concurrency is saturated (see `/observability/usage` open turns, p95) |
| Billing provider (Stripe) | % of revenue | When charging customers is approved |

## 2. What launches at $50

* Async accepted-then-reply chat with 1 s reply polling, durable turns,
  crash recovery and human-handoff fallback.
* Per-tenant fairness (2 concurrent turns per tenant, 4 global), monthly AI
  allowances, usage/health endpoint for staff, auto-refreshing handoff inbox.
* Capacity guide for one Starter API instance: sustained throughput is
  `4 turns / model_seconds` (≈1.3 turns/s, ≈4,800 turns/hour at 3 s per turn),
  far above pilot volume. Bursts queue rather than fail: 20 messages sent in the
  same instant were all answered, with p95 reply ≈17 s (benchmark in the
  architecture doc). Today's synchronous path fails 17 of 20 in that burst.

## 3. Staged rollout (each step = separate owner approval)

| # | Step | Gate / check | Rollback |
|---|---|---|---|
| 0 | Review and merge this draft PR (its prerequisites #3–#12 are already on master) | Owner review, CI green | Revert merge |
| 1 | Run migration `1p9a0001` on Neon (additive: two nullable columns + check) via the documented Alembic path | `alembic current` = `1p9a0001`; production preflight passes | `alembic downgrade 1p5a0001` (drops only the new columns) |
| 2 | Deploy API with `PUBLIC_CHAT_ASYNC_ENABLED=false` | Existing widget behaviour unchanged; `/health`, `/ready` OK | Redeploy previous image |
| 3 | Upgrade Render API to Starter if it is on Free (**paid, $7**) | Owner approval of spend | Downgrade plan |
| 4 | Upgrade Vercel to Pro (**paid, $20**) and deploy frontend with `vercel.json` region `sin1` | Owner approval of spend; widget loads on RISPU/A1 domains; BFF latency drop in Vercel logs | Remove `regions`, redeploy |
| 5 | Set `PUBLIC_CHAT_ASYNC_ENABLED=true` | Watch `public_chat_turn_completed`, `public_chat_turn_failed`, `/observability/usage` p95 and open turns for 24 h on the RISPU pilot | Set back to `false` (server forces sync; new widget falls back transparently) |
| 6 | Set per-tenant allowance overrides where needed; set the OpenAI project hard limit | Usage report matches OpenAI dashboard within estimate error | Clear overrides |
| 7 | Onboard additional tenants | Per-tenant allowance + domain allowlist + frame-ancestors entry | Disable widget per tenant |

No new production worker or scheduler is required at any step.

## 4. Risks and mitigations

| Risk | Mitigation |
|---|---|
| In-process turns are lost on deploy/restart | Durable job row; recovered on visitor poll, on start-up, and by the scheduled worker; covered by `test_turns_survive_killed_processing_tasks` |
| Multiple API instances | Fenced claim + per-session advisory lease make concurrent dispatchers safe; global concurrency becomes per-instance |
| Cost estimate drift (`estimated_cost_usd` is token-price based; embeddings not metered) | Keep OpenAI project hard limit as the backstop; reconcile monthly |
| Burst queueing (p95 reply grows with simultaneous turns beyond 4) | Follow-up: release the DB connection during the model call, then raise `PUBLIC_CHAT_TURN_MAX_CONCURRENCY`; watch `/observability/usage` p95 and open turns |
| Pre-existing pool hold-and-wait in the sync path (`analyze` takes a 2nd connection) | Async path bounds it; sync stays available only for old widgets and the kill switch |
| 1 s polling load | One indexed query per poll, only while a reply is pending; hidden tabs ≥30 s |
| Neon free compute exhaustion under heavy traffic | Watch Neon CU-hours; move to paid Neon before adding tenants beyond pilot scale |
| Vercel Hobby terms | Pro required before charging or onboarding commercial tenants |
| Merge conflicts with developer's local edits to `tests/test_validate_production_config.py` | Resolve at merge time; the change only moves the pinned head |
