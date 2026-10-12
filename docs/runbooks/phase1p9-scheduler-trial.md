# Phase 1P.9 — Reliable worker scheduling: controlled 14-day trial

**Status: prepared, NOT activated.** Nothing in this document has been
executed. No Render service exists, no secret has changed, and the GitHub
schedule is untouched. Every step marked *owner* needs explicit approval.

## 1. Why

`production-worker.yml` is configured `*/5`. Measured from 2026-10-07T16:34Z to
2026-10-12T16:18Z it executed in **22 of 1437** windows (1.53 %), with a
longest gap of 441.8 min. At the end of that window the dead-man state was
**`absent`**: 498 min since the last success. Every run that did start
succeeded in ≈45 s. The problem is **trigger delivery**, not the worker.
Reproduce with:

```bash
.venv/bin/python scripts/audit_workflow_schedule.py --repo zrasooli94/cxops-ai --workflow production-worker.yml --since 2026-10-07T16:34:50Z --markdown
```

## 2. Render facts this plan relies on

| fact | source | status |
| --- | --- | --- |
| Cron schedule is a cron expression in UTC | [render.com/docs/cronjobs](https://render.com/docs/cronjobs) | verified |
| "at most one run of a given cron job is active at a given time"; a due run is delayed until the active run finishes | same | verified |
| A new deploy "does not affect in-progress runs (only future runs)" | same | verified |
| Runs are stopped after 12 hours | same | verified |
| Environment groups can be shared with a cron job | same | verified |
| Run history and logs per run in the dashboard | same | verified |
| Billing "prorated by the second", "minimum monthly charge of $1 per cron job service" | same | verified |
| Per-second price of each compute plan | pricing page did not render for automated reading | **owner to confirm** |
| Public API to *list* cron runs | only third-party connector docs mention `POST /v1/cron-jobs/{id}/runs` (trigger) | **unverified**; plan does not depend on it |
| Schedule firing punctuality | not documented either way | **measured by this trial** |

**Render's single-run guarantee is per cron job only.** It does not stop a
separate GitHub runner from running at the same moment. Overlap safety comes
from the data layer (§4).

## 3. Preconditions (all required before the trial starts)

1. PRs **#3 → #4 → #8 → #9** are merged and deployed. Without #8/#9, two
   schedulers make the stale-claim races reachable (lost-claim overwrites,
   concurrent delivery of one reply, marker beyond the scanned window).
2. The production affected-job decision is complete (review package §9, PR #11
   `--operator-report`). Deploying #3/#4 re-executes affected `retry` rows.
3. The production `DATABASE_URL` used by the worker is a **direct** Neon
   endpoint, not a `-pooler` (PgBouncer transaction mode) host. The per-reply
   delivery lease (#8) and the SLA scanner both use **session-level** advisory
   locks, which are unsafe behind a transaction pooler. *Owner verifies.*
4. Dead-man alerting (§6) is configured, or explicitly deferred by the owner.

## 4. Data-layer invariants (why two schedulers cannot double-act)

| # | invariant | mechanism | test |
| --- | --- | --- | --- |
| I1 | a job is claimed by at most one worker at a time | `SELECT … FOR UPDATE SKIP LOCKED` | `test_job_fault_injection.py::test_f09_*` |
| I2 | a worker whose claim was lost can never write the job | single fenced `UPDATE … WHERE status='processing' AND locked_at=:claim` (#8) | `test_claim_fencing_concurrency.py::test_reclaim_between_read_and_write_*` |
| I3 | a lost claim never overwrites a newer worker's message state | fenced `fail_claimed` + `delivery_status <> 'sent'` conditional writes (#8) | `…::test_lost_claim_cannot_overwrite_newer_delivery_state`, `…::test_failure_sync_cannot_overwrite_*` |
| I4 | at most one worker delivers a given reply at a time | session-level advisory lease per message (#8) | `…::test_concurrent_delivery_of_one_reply_sends_once`, lease leak and contention tests |
| I5 | a reply is sent only if a complete scan proves the marker absent | cursor-paginated, bounded, fail-closed scan (#9) | `test_zendesk_marker_scan.py` |
| I6 | one SLA scan at a time | `pg_try_advisory_lock` (existing) | `test_worker_one_shot.py` |
| I7 | an expired session is finalized once | row lock + revalidation + idempotent lifecycle (Phase 1P.7.3B) | expiry suites |
| I8 | two full one-shot runs over one queue send each reply once | I1–I5 together | `test_dual_scheduler_invariants.py` (#9) |

## 5. Trial procedure

### 5.1 Create the Render Cron Job (*owner*, not done)

- Name `cxops-worker-cron`, region as the API (Singapore), same repo and branch
  `master`.
- Runtime: Python, with `PYTHON_VERSION=3.11` to match CI and the GitHub
  worker. Build: `pip install -r requirements.txt`.
- Schedule: `2-59/5 * * * *`. The offset avoids the top-of-hour load GitHub
  warns about and staggers away from the GitHub tick.
- Command (identical bounds to GitHub):
  `python scripts/preflight_production_env.py && python -m scripts.worker --once --max-jobs 25 --max-seconds 240 --finalize-expired-public-chat --expiry-batch-size 50`
- Environment:
  - the worker set from `docs/runbooks/production-environment-contract.md`;
  - the same Neon **direct** `DATABASE_URL`;
  - `WORKER_VERSION` set to the deployed commit, so `worker_once_summary`
    identifies the runtime;
  - `WORKER_METRICS_PORT=0`.
- Compute: the smallest plan. Measured runs use ~45 s and little memory.

### 5.2 Run both for 14 days (GitHub schedule unchanged)

- Daily, capture:
  - GitHub runs (`audit_workflow_schedule.py`, read-only);
  - Render run history (dashboard export, as JSON lines `{started_at,
    finished_at, status}`);
  - `worker_once_summary` log lines from both.
- Compare:

```bash
.venv/bin/python scripts/compare_scheduler_coverage.py --source github=github-runs:gh.jsonl --source render=summaries:render-summaries.jsonl --since <trial-start> --markdown
```

  The tool reports per scheduler, and combined:
  - coverage, missed and late windows, longest gap;
  - duration;
  - deadline exhaustion and backlog;
  - retries and failures;
  - degraded and unsuccessful runs;
  - minutes since the last success and the dead-man state.

### 5.3 Decision gate (*owner*)

Cut over only if, over the 14 days:

- Render coverage is **≥ 99 %** of 5-minute windows, with longest gap ≤ 15 min;
- 0 unsuccessful runs attributable to the scheduler;
- 0 duplicate customer-visible effects: no reply job with more than one
  public marker comment, and no `claim_lost` storms in summaries;
- deadline exhaustion is rare (no backlog trend).

### 5.4 Cutover (*owner*)

1. Remove only the `schedule:` trigger from `production-worker.yml`. Keep
   `workflow_dispatch` as the manual fallback.
2. Merge. Render becomes the sole scheduler.

### 5.5 Rollback (any time)

- **During the trial:** suspend the Render cron job. GitHub continues
  unchanged.
- **After cutover:** restore the `schedule:` trigger (revert the cutover
  commit), then suspend Render.

There are no schema changes and no data migration. Both directions are
config-only.

## 6. Dead-man's-switch design (missing success within 15 minutes)

Two signals, kept separate so that "nothing ran" and "it ran badly" are
different alerts:

| state | meaning | alert |
| --- | --- | --- |
| `absent` | no run **started** in the last 15 min (scheduler or start-up failure) | page: worker not running |
| `unsuccessful` | latest run started, but its summary says nothing succeeded (or the run record failed) | page: worker failing |
| `degraded` | latest run partly failed (retries, failures, lost claims, failed SLA scan, partial expiry) | ticket: investigate within business hours |
| `unknown_health` | a run record exists without a `worker_once_summary` | ticket: observability broken |
| `ok` | healthy run within 15 min | — |

`compare_scheduler_coverage.deadman_state()` implements exactly this
classification and is tested. Wiring it to an alert channel needs an owner
decision. Options:

- **(a)** A heartbeat URL (healthchecks-style) pinged at the end of each run,
  with `/fail` for `unsuccessful`. Needs a new secret and an outbound call
  from the worker. Smallest code change, but a new external dependency.
- **(b)** A Render log-based alert on `worker_once_summary` absence and level.
- **(c)** A scheduled read-only check that runs `deadman_state` over captured
  summaries.

Recommended: (a) after cutover, (b) during the trial.

## 7. Cost model

- Per run: about 45 s of compute.
- At one run every 5 min: 288 runs/day, so about 3.6 h/day or 108 h/month,
  billed per second at the chosen plan's rate.
- Render bills at least $1/month per cron service.
- During the trial, GitHub minutes stay free (public repository).
- The owner confirms the plan rate in the Render dashboard before creating the
  service.

## 8. Owner approvals required

1. Merge and deploy #3 → #4 → #8 → #9 (and #11's production diagnostic run
   first).
2. Confirm the worker `DATABASE_URL` is a direct, non-pooler Neon endpoint.
3. Create `cxops-worker-cron` (§5.1), including adding the secrets to Render.
4. Choose a dead-man wiring option (§6).
5. Decide at the gate (§5.3); the cutover is a separate PR.
