# Phase 1P.8 — Production Worker Reliability, Recovery Assurance & Scheduling

Owner-review package. Every statement below cites a test, a command output, or a
document. Where the evidence ends, the text says so.

## 1. Executive summary

- **The production schedule is the dominant reliability problem.**
  `production-worker.yml` is configured `*/5`. From 2026-10-07T16:34Z to
  2026-10-12T16:18Z it ran on **22 of 1437** windows (**1.53 %**). The median
  gap between runs was **5.3 h** and the longest **7.4 h**. Every run that
  started **succeeded** in about 44 s. The cause is missing schedule triggers,
  not failing, queued or slow runs (§7).
- **Proven code defects beyond PR #3 were fixed** in PR #4. Each has a
  regression test that fails on the previous code: **20 of 29** fault-injection
  scenarios failed on PR #3's code, plus 1 more after the PR #3 review. The most
  serious defects could **send a customer reply twice** (§3.2).
- **Developer environment confusion is now caught** before it causes damage
  (PR #5).
- **The affected local jobs are classified, and none needs a Zendesk send**
  (§9). Production rows are **unknown**: that check needs owner approval.
- **Nothing was merged, deployed, scheduled or replayed.** No production
  database or credential was used.

## 2. Baseline and provenance

| item | value |
| --- | --- |
| master baseline | `cf4d82e79daaf9a2994f107bcb3c7966c2e2c647` |
| PR #3 (MissingGreenlet fix) | `45d52a3`, unchanged, CI 7/7 green |
| PR #4 lifecycle hardening (stacked on #3) | `48b7321`, `bacd8fe` |
| PR #5 developer environment preflight | `add262f` |
| PR #6 scheduler audit tool | `9e4cd15` |
| PR #7 (this package + recovery diagnostic) | see PR |
| local runtime | Python 3.12.13 (`.venv`), plus a Python 3.11.15 scratch env for compatibility |
| test database | disposable Postgres container `cxops-phase1p8-disposable-test` (pgvector pg16, port 55432, DB `cxops_disposable_test`), migrated to head `1p5a0001` |

The developer's checkout and local dev database were never modified. Work used
separate git worktrees. The local dev DB was read only inside `READ ONLY`
transactions, and a fingerprint of `integration_jobs` was identical before and
after.

## 3. Confirmed defects and root causes

### 3.1 PR #3 (independent review): confirmed

- **Root cause confirmed.** `AsyncSession.rollback()` expires every loaded
  instance; `expire_on_commit=False` only covers commits. The non-retryable
  `conversation.reply` branch then read `message.organization_id`. That
  triggers an implicit synchronous refresh, so `MissingGreenlet`, so the worker
  treated the job as retryable.
- **Tenant matching after reload:** the message is re-read with the job's
  persisted `organization_id`, and the job by its own id. Correct.
- **Missing rows after rollback:** handled in code but untested. PR #4 adds
  both tests. A vanished job was also reported as *completed* (fixed in
  `bacd8fe`).
- **Gap PR #3 did not cover:** no claim fencing. A worker whose claim had been
  recovered could still overwrite the newer claim (fixed in PR #4, §3.3).
- **Non-retryable never becomes retry:** verified by F01/F02/F04/F12.
- **Exactly-once unchanged by #3:** correct, but pre-existing defects existed
  (§3.2).

### 3.2 Exactly-once external delivery (duplicate customer replies)

| # | defect | how found |
| --- | --- | --- |
| D1 | `execute` set the message to `sending` **before** `deliver()` checked for an already-`sent` message, which defeated the guard. A re-run then depended entirely on the marker scan. | F11 fails on #3's code |
| D2 | Marker scans read Zendesk's default **oldest-first** page, at most 100 comments ([Zendesk List Comments](https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_comments/)). On longer tickets the just-written marker is not on that page, so a retry **sends again**. | F10: 2 sends on the old code |
| D3 | The reply pre-send scan **failed open**: a scan error meant "not found", so the reply was sent again. | F16 |
| D4 | Provider 400/403/404/409/422 rejections were retried 5×. | F04 |

Finding without a defect: SLA first-response recording inside delivery commits
right after the provider write (`ServiceEscalationService` commits). Local
`sent` therefore often becomes durable before the job completes. That is safe,
and it is why D1 needed D2 to cause a duplicate.

### 3.3 Job lifecycle

| # | defect | evidence |
| --- | --- | --- |
| D5 | Stale recovery after **60 s** could re-execute a job that was still running (the one-shot budget is 240 s; the job timeout is 6 min). | F08 |
| D6 | A stale job that had used `max_attempts` was recovered forever (poison pill). | F08; local dev row `888` at `attempts=6/5` |
| D7 | No claim fencing on `mark_completed`/`mark_failed`. | F15 |
| D8 | Structurally invalid jobs (no tenant binding, foreign or missing message, unknown type, unsupported AI target) burned all retries. | F12, F13 |
| D9 | `handle_failure(job=None)` raised `AttributeError`; this was the long-standing mypy error in `scripts/worker.py`. | mypy now clean |
| D10 | `ai.evaluation` import failure leaked raw text and bypassed sanitization. | F13 |
| D11 | A job deleted mid-run was counted as **completed**. | F01 (vanished job) |

### 3.4 Rollback discipline (same class as PR #3)

D12. Duplicate-collision handling called `db.rollback()` inside larger units of
work at these sites:

- conversation thread create;
- initial message;
- provider comment batch;
- agent reply mirror (on the `agent.execute` path, *after* the Zendesk write);
- service escalation create;
- SLA scanner.

Each call expired every loaded object (→ `MissingGreenlet`) and **silently
discarded the caller's other pending work**. Proven by the five F05 tests.

## 4. Implemented fixes (PR #4)

- **SAVEPOINT helper:** `app/repositories/savepoint.py` provides
  `insert_in_savepoint`, used at every D12 site.
- **Delivery:**
  - removed the premature `sending` pre-mark (D1);
  - `ZendeskClient.get_ticket_comments(newest_first=True)` is used by both
    marker scans (`sort_order=desc`) (D2);
  - the pre-send scan fails closed with a retryable error (D3);
  - `ZendeskAPIError.status_code` plus a permanent-rejection set (D4).
- **Job repository:**
  - `STALE_PROCESSING_SECONDS = 600` (D5);
  - stale jobs with exhausted attempts are failed (D6);
  - `claimed_at` fencing on terminal writes (D7);
  - `mark_failed(terminal=True)`.
- **Job service:** `PermanentJobError(ValueError)` (D8); sanitized `ai.evaluation`
  import path (D10).
- **Worker:**
  - `JobResult` outcome per job;
  - `handle_failure` guard (D9) and vanished-job outcome (D11);
  - run summary (§8).

Not changed: tenant scoping, provider permissions, agent autonomy, the
production workflow, the schedule, expiry behavior, migrations.

## 5. Verification matrix

### 5.1 Fault injection: `tests/test_job_fault_injection.py`

Uses a fake Zendesk that honours real paging. A real network call fails the
test. Global-claim tests run only on a disposable DB (`CI=true` or a database
name ending in `_disposable_test`).

| scenario | protected invariant | on #3's code | on PR #4 |
| --- | --- | --- | --- |
| F01 invalid target | non-retryable → terminal, no send | pass | pass |
| F01 message/job vanish after rollback | no crash; vanished job never "completed" | — / fail | pass |
| F02 unsupported provider | terminal, no send | pass | pass |
| F03 transient network | retry + backoff, message `retrying`, no send | pass | pass |
| F04 permanent rejection 422 | terminal `provider_rejected` | fail | pass |
| F04 429/500/503 | stay retryable | fail\* | pass |
| F05 ×5 duplicate collisions | loaded objects intact, pending work kept | fail | pass |
| F06 commit lost after provider accept | reconcile, exactly one send | pass | pass |
| F07 process dies after send | stale re-run reconciles, one send | pass | pass |
| F08 live long job | not stolen at 120 s | fail | pass |
| F08 crashed job | recovered after window | pass | pass |
| F08 poison job | stops at `max_attempts` | fail | pass |
| F09 concurrent claims | each job claimed exactly once | pass | pass |
| F10 marker beyond first page | found newest-first, one send | fail | pass |
| F11 repeated execution | sent reply → zero provider calls | fail | pass |
| F12 cross-tenant payload / no binding | terminal, foreign row untouched | fail | pass |
| F13 AI target / dependency failure | terminal / bounded sanitized retry | fail | pass |
| F14 capacity bound | unclaimed jobs untouched | pass | pass |
| F15 lost claim | stale worker cannot overwrite | fail | pass |
| F16 scan failure | fail closed, no send | fail | pass |

\* On the old code these fail only because `ZendeskAPIError` had no
`status_code` argument; the old *behaviour* (retry) was already correct.

Totals: 31 scenarios; **0 failed** on PR #4. 20 failed on #3's code, plus the
vanished-job test added during review.

### 5.2 Suites (local, disposable DB)

| scope | result |
| --- | --- |
| PR #4 full unit suite (`-m "not integration"`, 3.12) | 1963 passed at `48b7321`; see the PR for the final head |
| PR #4 relevant suites on Python 3.11 | 194 passed |
| PR #4 worker + fault injection after the review commit | 89 passed |
| PR #5 full unit suite | 1936 passed; preflight tests 27 passed on 3.11 and 3.12 |
| PR #6 audit tests | 20 passed on 3.11 and 3.12 |
| PR #7 diagnostic tests | 22 passed |
| Ruff 0.16.10 `--isolated` (app tests scripts alembic) | pass on every branch |
| mypy `app` (+ `scripts/worker.py`) for 3.11 and 3.12 | pass, including the formerly failing worker file |
| `git diff --check` | clean on every branch |
| GitHub CI | per-PR status in the final report |

## 6. Tenant isolation and transaction safety

- Message reloads are scoped to the job's persisted tenant. A job rebound to
  another tenant fails terminally without touching the foreign row (F12).
- The global claim stays the only cross-tenant read. Claiming uses
  `FOR UPDATE SKIP LOCKED` and was verified under concurrent sessions (F09).
- Terminal job writes are fenced by the claim token (F15).
- Duplicate collisions no longer roll back the caller's transaction (F05).
- The diagnostic runs in a verified `READ ONLY` transaction. A write attempted
  inside it is rejected by Postgres (test).

## 7. Scheduling reliability analysis

### 7.1 Evidence

The audit is a read-only `scripts/audit_workflow_schedule.py` run over the
GitHub API.

| metric | value |
| --- | --- |
| window (UTC) | 2026-10-07T16:34:50Z (workflow added) → 2026-10-12T16:18Z |
| runs | 22 `schedule` + 2 `workflow_dispatch`; 24/24 success |
| expected 5-min windows | 1437 |
| coverage | **1.53 %** |
| interval median / p90 / p95 / max | 318.6 / 413.7 / 427.8 / **441.8 min** |
| longest gap | 2026-10-08T07:30Z → 14:51Z |
| missing windows between runs / after last run | 1259 / 100 (trailing gap 498 min) |
| longest consecutive missing | 87 windows |
| queue delay / job duration / worker step | ≈0 s / median 43.5 s, max 82 s / median 10 s |
| timeouts / cancellations | 0 / 0 |

What the API cannot show: triggers that never fired leave no record, so the
*reason* for each missing window is not observable. Trigger offset is reported
only as a lower bound.

### 7.2 Interpretation

GitHub documents that a scheduled event "can be delayed during periods of high
loads" and that "some queued jobs may be dropped". It also says schedules run
only on the default branch and that public-repository schedules are disabled
after 60 days without activity
([docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)).

The repository is public, so Actions minutes are not the limit. The workflow is
`active`. Every run that started succeeded quickly. The evidence therefore
points to **trigger loss/throttling on GitHub's side**, not to anything in this
repository's code. Moving the cron minute away from `:00` (GitHub's own advice)
is cheap, but cannot plausibly lift 1.5 % coverage to near 100 %.

### 7.3 Alternatives

| option | reliability | cost | security | overlap | maintenance | rollback |
| --- | --- | --- | --- | --- | --- | --- |
| **A. Render Cron Job** running the existing one-shot command | Dedicated scheduler. Render guarantees "at most one run of a given cron job is active at a given time" and delays a due run rather than overlapping ([docs](https://render.com/docs/cronjobs)). On-time firing is not documented as guaranteed. | Billed per second, "$1" minimum per cron service per month (Render docs; confirm on the account) | Same env contract as the Render API; secrets stay with Render | Built-in no-overlap; DB-level claims and row locks cover overlap with any other worker | None (existing command, existing preflight) | Suspend or delete the cron job |
| B. Render Background Worker (`scripts/start_render_worker.sh`) | Always-on, polling every 1 s; restart and deploy-overlap behavior **not documented** on Render's page | Paid instance (confirm plan pricing) | Same as A | Atomic claims; SLA advisory lock | **Requires code:** expiry runs only in `--once` today | Stop the service |
| C. External pinger → `workflow_dispatch` | Still GitHub runners; pinger is a third party | Low | **Needs a token with `actions:write` stored externally** | Same as today | Two systems | Remove the pinger |
| D. Keep GitHub schedule, shift the minute | Measured 1.5 % | Free | Unchanged | Unchanged | None | — |

### 7.4 Recommendation and plan (for approval; nothing activated)

Recommended: **Option A as primary, keeping the GitHub schedule as a secondary
safety net during a two-week trial.**

1. Create a Render Cron Job `cxops-worker-cron`, schedule `2-59/5 * * * *`
   (off the top of the hour), region as for the API. Start command:
   `python scripts/preflight_production_env.py && python -m scripts.worker --once --max-jobs 25 --max-seconds 240 --finalize-expired-public-chat --expiry-batch-size 50`.
   Environment: the worker section of `docs/runbooks/production-environment-contract.md`
   (the same Neon `DATABASE_URL`).
2. Concurrent runs from Render and GitHub are safe by design. Job claims are
   `SKIP LOCKED` and fenced (PR #4), the SLA scan uses an advisory lock, and
   expiry uses row locks with revalidation.
3. Measure for 14 days. Use `scripts/audit_workflow_schedule.py` for GitHub and
   Render's run history for Render. Use the `worker_once_summary` health from
   PR #4.
4. Decision gate: if Render coverage is at least 99 % of windows with no
   overlap incidents, remove the GitHub `schedule:` trigger (keep
   `workflow_dispatch`).
5. Rollback: suspend the Render cron job. The GitHub schedule is still there.

Option B is the later step if near-real-time processing is required. It needs
a bounded periodic expiry pass added to `run_worker` first, or expiry silently
stops.

## 8. Monitoring and alerting proposal

Implemented in PR #4:

- **`worker_once_summary`** event per one-shot run. Fields:
  `worker_version`, `run_id`, `run_attempt`, `health`
  (`healthy`/`degraded`/`unsuccessful`), `exit_reason`, `capacity_exhausted`,
  `sla_scan`, job counts (`claimed`/`completed`/`retried`/`failed`/`claim_lost`/`unknown`),
  `failure_categories` (exception class names), `expiry_*`, and
  `elapsed_seconds`. It is also written to the GitHub step summary.
  `worker_once_completed` is unchanged.
- **Health rules:**
  - `unsuccessful`: work was attempted and nothing succeeded, or expiry failed.
  - `degraded`: any retry, failure, lost claim, failed SLA scan, or partial
    expiry failure.
  - Capacity exhaustion is reported but is not a failure.
- **Exit code:** `--exit-nonzero-on-unsuccessful` (exit 3) is opt-in and **not
  enabled**. Exit 0 is no longer read as "every job succeeded".

Proposed (owner decision):

1. Dead-man's switch: alert if no `worker_once_summary` with a successful run
   is seen for 15 minutes. The schedule gap is otherwise silent.
2. Alert on `health != healthy` twice in a row, and on any `unsuccessful`.
3. Read-only SQL checks (dashboard or cron):
   - oldest `pending`/`retry` job age;
   - count of `failed` jobs per hour;
   - any job with `attempts > max_attempts` (should now be impossible);
   - expired live public-chat sessions older than 15 minutes.
4. After a trial, consider enabling `--exit-nonzero-on-unsuccessful` so GitHub
   marks the run red.

## 9. Recovery decision package (affected jobs)

`scripts/diagnose_affected_jobs.py` behaves as follows:
- **Read-only**, in a verified `READ ONLY` transaction.
- Refuses non-local databases unless `--allow-remote-read-only` is passed.
- Prints no bodies or payload content.

Run against the **local dev DB** (fingerprint unchanged):

| job | status | attempts | message | category | why |
| --- | --- | --- | --- | --- | --- |
| 887, 892, 929, 935 | retry | 1/5 | `retrying` | terminal, supervised fail | no `delivery_token` (legacy rows, 2026-09-22) → no marker, cannot be sent with exactly-once protection |
| 952, 957 | retry | 1/5 | `retrying` | terminal, supervised fail | no `delivery_token`, and no linked ticket |
| 888 | failed | **6/5** | `failed` | leave untouched | already failed; attempts overflow (D6 evidence) |

No local record needs Zendesk verification, and none should be sent.

### Decisions

- **Leave untouched:** job 888, and every record the tool marks
  `leave_untouched__investigate`.
- **Eligible for supervised reconciliation:**
  `terminal_nonretryable__supervised_fail`. Set the job to `failed` and the
  message to `failed` with a `target_unavailable` code. No send.
- **Requires external verification:** `external_verification_required` (none
  locally). Look for the reply marker on the Zendesk ticket **before** any
  action. If present, reconcile to `sent`; if absent, a human decides whether a
  days-late reply should go out at all.
- **Idempotency demonstration:** before any reconciliation, run the diagnostic
  again and keep its JSON. Apply changes only by job id, with an optimistic
  predicate (`status` and `attempts` equal to the snapshot), then re-run the
  diagnostic and diff it. No provider call is involved in any supervised fail.

### Approvals and safety

- Owner approval is required for each category, and separately for running the
  diagnostic against production (`--allow-remote-read-only`).
- **Important:** deploying PR #3/#4 changes behavior for any existing
  production `conversation.reply` job in `retry`. The next worker run
  re-executes it:
  - jobs whose blocking condition persists now **fail terminally without
    sending** (safe);
  - a job whose target has since become valid **would be delivered**, possibly
    days late.

  **Run the diagnostic against production read-only before merging #3/#4.**
- Rollback and verification: snapshot affected rows before any change, apply
  updates in a single transaction scoped by id, verify with a diagnostic diff,
  and keep the snapshot for reversal.
- Separate local hazard (not production): the local dev DB holds 964 `pending`
  and 177 `retry` `ai.evaluation` jobs. Running the local worker against it
  would execute them (paid model calls). Do not run the local worker against
  that DB without a decision.

## 10. Unresolved risks

1. The production schedule remains at about 1.5 % coverage until §7.4 is
   approved.
2. Production affected-job state is unknown (no production access by design).
3. The reply exactly-once guarantee still relies on the marker being among the
   newest 50 comments. If more than 50 comments arrive between a send and its
   retry, the marker could be missed. The window is now minutes, not ticket
   lifetime.
4. A stale window of 600 s means a crashed job waits up to 10 minutes. This is
   deliberate, and reversible by constant.
5. Render background-worker restart and deploy semantics are not documented on
   Render's page; verify before Option B.
6. Python 3.11 compatibility was verified with a scratch 3.11 environment. CI
   (3.11) is the authority.

## 11. Deployment and rollback plan

**Merging to `master` is deploying.** The scheduled worker checks out `master`
on every run.

1. Merge #3, then #4 (stacked; no migrations). The next worker run uses them.
   **Precondition:** the production affected-job diagnostic (§9).
2. #5, #6 and #7 change tooling and docs only; merge in any order.
3. Rollback: `git revert` the merge commit on `master`. No schema change, so
   rollback is immediate on the next run. The 600 s stale window and the claim
   fencing revert with it.
4. Verify after merge: the next `worker_once_summary` is present and `healthy`,
   the audit tool shows the run, and there are no new `failed` reply jobs with
   unexpected categories.

## 12. Acceptance criteria for production approval

- [ ] CI green on #3, #4, #5, #6 and #7 at their final heads.
- [ ] Owner has reviewed the fault-injection matrix (§5.1).
- [ ] Production affected-job diagnostic run read-only and its categories
      decided (§9).
- [ ] No production `conversation.reply` job in `external_verification_required`
      is left unresolved before the merge.
- [ ] Scheduler decision taken (§7.4). If Render is chosen, the cron job is
      created **after** #3/#4 are on `master`.
- [ ] Alerting from §8 items 1–2 configured, or explicitly deferred.
- [ ] First post-merge `worker_once_summary` is `healthy`.

## 13. Recommended next milestone

**Phase 1P.9: reliable scheduling and operational alerting.**

1. Implement the approved scheduler (§7.4) and its 14-day measurement.
2. Add a bounded periodic expiry pass to the continuous worker so Option B
   becomes safe.
3. Add a dead-man's switch and health alerts.
4. Strengthen the reply marker scan (paginate until the send time instead of a
   fixed 50).
5. Carry out the supervised recovery of production affected jobs, if any.
