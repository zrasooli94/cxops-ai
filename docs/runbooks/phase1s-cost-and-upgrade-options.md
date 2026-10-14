# Phase 1S - cost position and upgrade options (nothing purchased)

Target: monthly operating cost <= $50. Baseline plans are those recorded in
`phase1p9-budget-and-rollout.md`; confirm the actual plans in each vendor
dashboard before relying on this table. Prices re-checked October 2026 from
vendor pages: Neon (neon.com/pricing) and OpenAI model pages were retrievable;
Render's pricing page is script-rendered and could not be read, so its figures
are from the 1P.9 doc and Render's compute-plan documentation (specs) and must
be confirmed on the dashboard.

## 1. Current position

| Item | Plan | $/month |
|---|---|---|
| Vercel (frontend/BFF/widget) | Pro, 1 seat | 20 |
| Render API | Starter, 512 MB / 0.5 CPU, always-on | 7 |
| Neon PostgreSQL | Free (100 CU-h/project, suspends after 5 min idle) | 0 |
| Nhost (staff auth) | Free | 0 |
| GitHub Actions backstop worker | Free minutes | 0 |
| OpenAI (gpt-4o-mini $0.15/$0.60 per M tokens; embeddings $0.02 per M) | usage, hard-capped by tenant allowance, platform ceiling and project limit | ~5-12 |
| **Total** | | **~32-39** |

Phase 1S effect on cost: none upward. One fewer embedding per grounded turn
(1R) and no extra model calls; the new timeouts can only shorten a failed
call. Tenant budgets, the platform AI ceiling (`PLATFORM_MONTHLY_AI_BUDGET_USD`),
reservations and the async-disabled state are untouched.

## 2. Options, with the measurement that justifies each

| # | Option | +$/month | Total | Buy only if (measure first) |
|---|---|---|---|---|
| 0 | Put Render and Neon in the **same region** (dump/restore to a new Neon project in Render's region, new `DATABASE_URL`) | 0 | ~32-39 | `/ready` `timing_ms.database_round_trip` > ~20-30 ms. Cheapest and biggest lever: ~46 statements per turn each pay it. Needs a maintenance window and owner approval |
| 1 | Neon **Launch**, scale-to-zero disabled, 0.25 CU always-on (~0.25 x 730 h x $0.106 + storage) | ~19-20 | ~51-59 | Cold-first-message wake time is the dominant complaint **and** the same-region fix did not remove it. **Exceeds $50** unless OpenAI < ~$4; would need another line item to drop |
| 2 | Neon Free + business-hours keep-warm ping (e.g. 08:00-20:00 every 4 min) | 0 | ~32-39 | Only after measuring. 0.25 CU x 12 h x 30 d = 90 CU-h of the 100 CU-h free allowance: almost no margin. If exhausted the compute is disabled until reset, which would take the product down - not recommended without alerts |
| 3 | Render Standard (1 CPU / 2 GB) | +18 | ~50-57 | Sustained CPU > 80% on Starter, or `application-side` turn time > 1 s with DB RTT already low. Locally app time is ~0.2 s/turn, so expect not to need it |
| 4 | Second API instance | +7 | ~39-46 | Open-turn queue saturated (`/observability/usage`) at pilot-plus volume |
| 5 | Staff notification (email/Slack webhook on handoff) | 0-low | unchanged | Product decision, not capacity: today no one is notified |

## 3. Measurable triggers to record before any purchase

* `GET /ready` -> `timing_ms.database_round_trip` (steady RTT) and
  `database_first_statement` (cold wake) from the Render process.
* `cxops_public_chat_turn_stage_seconds{stage}` p50/p95 per stage and the
  `public_chat_timing` log (`agent_ms`, `rag_ms`, `allowance_ms`).
* `cxops_public_chat_answer_decisions_total{outcome}` - share of turns that
  are `grounded_reply` vs `skip_*` / `ineligible_*`.
* Render service events (restarts/deploys) around any >30 s first response;
  `/version.uptime_seconds` on a slow request.
