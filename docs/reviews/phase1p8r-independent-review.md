# Phase 1P.8R — Independent safety review and readiness

Second review of the Phase 1P.8 work. It assumes the first review and its tests
could share blind spots.

**Method:**
- Deterministic two-session interleavings with explicit `asyncio.Event`
  barriers against **real PostgreSQL** (disposable container), with no sleeps.
- Fake providers only; any real network call fails a test.
- Every fix is proven by a test that fails on the previous implementation.
- A separate reviewer agent independently read the concurrency and
  delivery code (`bacd8fe..74e4997`).

**Baseline:** `master` `cf4d82e`. Nothing merged, deployed or activated.

## 1. Verdict

**Not labelled "certified."** All reproduced duplicate-send, state-overwrite
and cross-tenant defects are fixed and covered by failing-before /
passing-after tests. Residual customer-visible risks remain (§4) that code in
this repository cannot fully remove, notably marker tampering by a human. They
need owner acceptance.

**Ready for owner review and the merge sequence:** #3 → #4 → #8 → #9 (worker
code); #6 → #12, #7 → #11, #5, #10 (tooling, tests, docs).

## 2. Findings

Severity is the customer-visible worst case. "Before" names the code the
failing test ran against.

| ID | sev | finding | evidence (fails before → passes after) | fix |
| --- | --- | --- | --- | --- |
| R1 | **critical** | Two workers delivering one reply (a claim recovered while the first still runs) both scan before either writes, so **two customer replies** are sent | `test_concurrent_delivery_of_one_reply_sends_once`: A=delivered, B=delivered on #4 | #8 per-reply advisory lease |
| R2 | **critical** | Marker scan read only the newest 50 comments; with the marker at positions **51/100/101/250** a recovery **re-sent** the reply | depth probe on #8: 4 duplicates; `test_recovery_finds_the_marker_at_any_depth_without_resending` | #9 complete cursor scan |
| R3 | high | `mark_completed`/`mark_failed` checked the claim in Python, then wrote by id: a re-claim in between was overwritten | `test_reclaim_between_read_and_write_cannot_be_overwritten` ×2 on #4 | #8 atomic fenced `UPDATE` |
| R4 | high | Non-retryable branch after a lost claim turned a newer worker's **delivered** job/message into `failed` | `test_lost_claim_cannot_overwrite_newer_delivery_state` ×2 on #4 | #8 `fail_claimed` + conditional writes |
| R5 | high | A stale identity-map copy made `deliver()` miss another worker's `sent` and **send again** | `test_reply_sent_by_another_worker_is_seen_despite_a_stale_identity_map` on #8 pre-review | #8 `0b51817` (re-read under lease) |
| R6 | medium | `handle_job_failure` turned a just-`sent` message back into `retrying` | `test_failure_sync_cannot_overwrite_a_reply_sent_meanwhile` on #4 | #8 conditional `UPDATE … <> 'sent'` |
| R7 | medium | Lease leaked onto a pooled connection when cancelled right after the grant, so that reply was blocked indefinitely | `test_lease_never_leaks_when_cancelled_right_after_the_grant` (pooled engine) | #8 `0b51817` (invalidate on unknown state) |
| R8 | medium | Lease contention was retried as an error, burning all attempts in about a minute and marking a reply `failed` while it was being sent | `test_lease_contention_defers_without_burning_attempts` | #8 `0b51817` `JobDeferredError` (attempt refunded) |
| R9 | medium | A private note quoting the reply marker counted as delivery: reply **never sent** but shown `sent` | `test_private_note_quoting_the_marker_is_not_proof_of_delivery` | #9 `3615ee2` (public comments only) |
| R10 | medium | `enqueue_reply` concurrent duplicate collided at the flush, outside the handler: **HTTP 500** instead of the idempotent duplicate, plus a MissingGreenlet-class read after rollback | `test_concurrent_duplicate_enqueue_returns_the_winner` on #4 | #8 `27622e6` |
| R11 | medium | `insert_in_savepoint` reported the caller's **own** pending unique violation as a lost race | `test_savepoint_never_misreports_the_callers_own_pending_conflict` on #4 | #8 `27622e6` (pre-flush) |
| R12 | medium | Recovery diagnostic read jobs and messages at different moments (READ COMMITTED) | `test_report_is_one_consistent_snapshot_under_concurrent_writes` fails under READ COMMITTED | #11 (REPEATABLE READ snapshot) |
| R13 | medium | Diagnostic silently truncated at `--limit` | `test_more_rows_than_the_limit_is_reported_incomplete` | #11 (INCOMPLETE, exit 3) |
| R14 | low | Diagnostic printed raw driver errors (host/user details) | `test_driver_errors_are_reported_without_details` | #11 |
| R15 | low | Flaky `customer_360` search test (random hex could spell `ada`) | 20,000-suffix property test; 40 repeated runs green | #10 |

**Checked and found correct:**
- under READ COMMITTED, a fenced `UPDATE` blocked on a re-claim's row lock
  re-evaluates after the commit and matches nothing (EvalPlanQual), observed
  via `pg_stat_activity` (`test_fenced_update_rechecks_after_waiting_on_a_row_lock`);
- `SKIP LOCKED` claims under concurrent sessions;
- normal dual-scheduler overlap (two full one-shot runs, expiry enabled):
  each reply is sent once. This also holds on #4; the races above need a
  stale claim;
- the marker boundary logic (marker checked before the boundary return;
  equal timestamps are not "unordered");
- tenant scoping of every provider call and write. The reviewer agent found no
  tenant gap.

## 3. Re-review of the 12 Phase 1P.8 defects

All twelve (D1–D12) are reproduced against **real PostgreSQL transactions**;
only providers are faked. Corrections:

- **D7** (claim fencing) was incomplete in #4: a read-check-write (R3/R4).
  Completed in #8.
- **D2/D3** (marker scan newest-first, fail-closed) were necessary but not
  sufficient (R2). Completed in #9.
- **D12** (savepoints) had an edge case: the caller's own conflict was
  misreported (R11). Fixed in #8.

## 4. Residual risks (owner acceptance needed)

1. **Marker tampering.** If a human edits or redacts the marker out of the
   delivered public comment before a retry, the scan proves absence and the
   reply is sent again. Only provider-side idempotency could remove this.
   Likelihood: requires a manual edit inside the retry window.
2. **Pooler requirement.** The delivery lease and the SLA scanner use
   **session-level** advisory locks. Behind a transaction-mode pooler
   (Neon `-pooler` host) they are unsafe. The owner must confirm the worker
   uses a direct endpoint (Phase 1P.9 runbook precondition 3).
3. **Bounded scan.** More than 2,000 comments between enqueue and a retry
   makes the scan inconclusive. The reply then fails terminally as
   `delivery_unverified` (no send) and needs an operator.
4. **Deferred loop.** A lease held indefinitely by a hung process (connection
   alive) keeps deferring that reply every 60 s without consuming attempts.
   Watch `deferred` counts in `worker_once_summary`.
5. **Mid-delivery commits** (OAuth refresh, SLA first-response) can make
   `sending`/`sent` durable before job completion. The lease and the complete
   marker scan keep this safe, but a crashed run may show `sending` until
   stale recovery (≤ 10 min).
6. **Intermittent test.** One full-suite run during certification, executed
   under concurrent Docker load, had a single uncaptured failure. It did not
   reproduce in 5 later full runs. The only wall-clock-bounded wait in the new
   tests was widened.

## 5. Release disposition

| PR | content | disposition |
| --- | --- | --- |
| #3 | MissingGreenlet fix | ready; merge first; deploy only together with #4, #8 and #9 |
| #4 | lifecycle hardening | **do not deploy alone**; merge, then #8 and #9 |
| #8 | atomic fencing, lease, review fixes | ready after #4 |
| #9 | complete marker verification, dual-run test | ready after #8 |
| #5 | dev-environment preflight | ready (independent) |
| #6 → #12 | schedule audit → trial preparation | ready (no activation) |
| #7 → #11 | review package → supervised recovery decisions | ready (read-only tooling and docs) |
| #10 | deterministic customer_360 fixtures | ready (independent) |
| #13 | integration certification | **do not merge**; close after the sequence |

## 6. Owner approvals required

1. Read-only production run of `diagnose_affected_jobs --operator-report
   --allow-remote-read-only`, and a decision per action group, **before**
   deploying #3/#4/#8/#9.
2. Confirm the direct, non-pooler `DATABASE_URL` for the worker.
3. Accept residual risks §4.1–4.5, or request provider-side idempotency work.
4. Merge the sequence; then the Phase 1P.9 trial approvals (runbook §8).
