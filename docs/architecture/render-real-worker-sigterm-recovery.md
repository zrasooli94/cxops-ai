# CXOps AI — Disposable OS-process interruption and recovery

**Status: SOURCE ONLY / RELEASE HOLD.** This reviewable addition is stacked on
draft PR #46 (itself stacked on draft PRs #45 and #44). It does **not** start,
resume, edit, or provision any Render service; does not touch the existing
production GitHub worker, database, credentials, API, tickets or Vercel
production deployment.

## Why this check exists

The preceding synthetic-load suite verified cancellation of an asyncio task
while a provider was blocked. That is not a process termination. Render may
send process signals or terminate a container; failures can occur after the
PostgreSQL claim is committed but before the completion transaction. Such a
job must remain unavailable to another worker before the stale threshold, then
be reclaimed safely after the threshold by the normal repository code.

## Scope of the new executable probe

Run `python -m scripts.worker_disposable_termination_probe` **only** in the
existing disposable GitHub Actions PostgreSQL 16 workflow.

1. A fail-closed, stdlib-only guard requires exact development-only flags, a
   CI environment, no provider secrets, and the exact loopback synthetic
   PostgreSQL database. The child inherits a deliberately allowlisted
   environment, not ambient host credentials.
2. Refuse a nonempty synthetic jobs/organizations database, create precisely
   one synthetic organization and job with max attempts = 3.
3. Launch a separate **real worker Python process** that uses the actual
   `run_worker_once` and PostgreSQL claim code. Substitute *only* the external
   provider executor with a fake that writes a marker to a temporary local
   file and then blocks indefinitely. No real provider SDK operation occurs.
4. Confirm that a committed `processing` claim and first attempt are visible
   from a **separate database session**, then issue an OS `SIGTERM` to the
   child. Verify it was signal-terminated, the job was **not** marked completed,
   and its first claim is still persisted.
5. Run the normal worker before the 600-second stale threshold: it must claim
   **zero** jobs and leave the processing row unchanged.
6. Age **only that disposable job** to 15 minutes old, then run the normal
   worker with a successful fake executor. Require one recovery execution,
   `completed` status, second attempt, no remaining lock, and an actual
   completion timestamp. Assert the synthetic tenant and payload on recovery.
7. Always terminate a lingering child and delete synthetic data, even after
   failure. The workflow also destroys its ephemeral PostgreSQL container.

The script reports only bounded JSON metadata. Its child stdout/stderr are
discarded rather than being reproduced in GitHub logs. All provider behavior
is replaced in the process under test; this test is not a network firewall
and cannot certify arbitrary future code has zero egress.

## Independent PASS criteria

- Exact-head GitHub Actions workflow concludes **success**, including the
  standalone signal probe, 167 existing focused regressions, memory probe,
  disposable guard and pinned Ruff.
- A child exits due to `SIGTERM` while inside the blocking fake.
- The early pass does not steal a nonstale claim; the aged pass completes
  exactly once with the correct tenant, attempt and fenced state.
- No credential, provider, paid service or persistent database is accessed.
- Any incomplete, timed-out or otherwise failed check is **NO-GO**. Never
  weaken signal or state assertions merely to produce a successful CI run.

## Limitations and production release gates

This experiment uses an ordinary Linux GitHub runner, not a Render Cron
instance and not a genuine platform stop/restart sequence. It does not show
Render's SIGTERM grace period, cgroup OOM behavior, per-provider timeouts,
external API side effects, platform billed runtime or database reconnection
under a real outage.

A successfully completed provider request followed by a worker crash **before
recording completion** may be attempted again after stale recovery; PostgreSQL
claim fencing prevents stale database commits but cannot undo or automatically
deduplicate a remote side effect. Qualified review must verify external API
idempotency/reconciliation per integration before production overlap.

Likewise, the 240-second one-shot deadline is tested **between claims**: it
does not preempt a slow provider operation already in flight. The nominal
five-minute period is **not** a hard runtime guarantee, and repeated lengthy
operations can increase backlog, active time and costs. A separately approved
Render-hosted synthetic resource/cancellation canary must measure actual
512 MiB cgroup memory, process termination grace period and scheduling
behavior without live providers or production credentials.

Before any production activation: independent qualified security review,
explicit owner authorization for the exact worker code and secret transfer,
Render cost and memory acceptance, controlled shadow/rollback tests, and
separate approval to merge or change GitHub scheduling. The inert Render
synthetic Cron `crn-dbauc361egvs73c65pgg` remains **SUSPENDED**.
