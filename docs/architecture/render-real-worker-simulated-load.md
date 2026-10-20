# CXOps AI — Representative simulated worker load (source-only)

**HOLD: This milestone performs NO production, Render, Cloudflare, Zendesk,
OpenAI, customer or live ticket action.** Stacked on the previously verified
disposable real-worker draft PR #45; builds on the existing one-shot
\`scripts.worker\` and its actual PostgreSQL job claim/fencing implementation.
The separate paid Render synthetic scheduler test was completed and its
service \`crn-dbauc361egvs73c65pgg\` was suspended on 2026-10-20 11:15 UTC.

## Scope and isolation

Use the existing offline GitHub Actions workflow
\`.github/workflows/render-real-worker-disposable.yml\` and the exact, ephemeral
PostgreSQL 16 target, exclusively at
\`127.0.0.1:5432/cxops_worker_probe_disposable\`. The pre-import fail-closed
guard requires GitHub Actions, the synthetic opt-in flag, development-only
runtime, DEBUG=false and no actual provider credentials or remote DSN.
No new GitHub secret, database deployment, API route, migration, scheduled
service or environment group is created. CI deletes synthetic objects and
destroys the service container after the run.

Every synthetic execution replaces **only** the external
\`IntegrationJobService.execute\` call; the worker's real
\`run_worker_once\`, job repository, SQL row claims, real status transitions,
attempt counters and claim fencing are exercised. Integration providers
are never contacted.

## Scenarios and objective pass/fail evidence

1. **Backlog, two simultaneous worker drains and a third catch-up:** seed
   65 synthetic jobs in two synthetic organizations. Run two real one-shot
   instances concurrently with a max-25-job bound; finish the remaining
   15 in a third bounded pass. Simulate 12ms provider latency per job.
   Require 65 unique executions, exactly one completion each, no invalid
   status, no misplaced tenant and no duplicate attempts.
2. **Slow provider exceeds one-shot deadline:** four jobs, first fake provider
   spends 550ms while the worker max-seconds budget is only 300ms. It must
   finish the already claimed job, stop claiming more, and allow the next
   run to finish the other three. Check no stranded processing row.
3. **Transient provider failure storm and backoff:** twenty independent
   synthetic jobs fail once and succeed on attempt two. The actual
   repository records the retry and fenced completion. Only the disposable
   test advances the timestamp of its own retry rows to avoid waiting for
   wall-clock backoff; permanent production retry rules remain unchanged.
   Require exactly two invocations per job, no third attempt and terminal
   successful statuses for all.
4. **Interrupted process-task and stale recovery:** start one real one-shot
   drain, stop it while a fake provider waits, verify the job remains
   processing with no false completion, then advance only its synthetic
   locked_at timestamp beyond the documented 600-second stale threshold.
   The next real worker pass must reclaim and complete it, with attempts
   incremented and no duplicate success. This models in-flight task
   cancellation, NOT an actual Render machine kill/restart.
5. **Actual worker and synthetic 160 MiB provider response:** a separate
   Python process imports the real worker after a strict local DB guard,
   seeds six synthetic jobs and uses an in-memory fake provider which
   commits 160 MiB of response-buffer pages per sequential job, with
   50ms simulated latency. The real worker performs real SQL claims, one
   SLA scan and empty chat-expiry finalization. The parent captures only a
   whitelisted JSON report (never raw application output). The test
   passes only if all six jobs complete once, zero actual provider calls,
   no unapproved DB side effects, normal exit and measured child peak
   RSS **below 384 MiB** (25% headroom below the 512 MiB Render plan).
   Resource usage is measured in the test runner, not on actual Render.

Each scenario is bounded to seconds, with test-level timeouts and a
CI 18-minute job timeout. No job input can select a destination URI or read
real provider credentials. The existing one-shot and expiry regression
suites still run without disabling or weakening any security controls.

## Interpretation and limitations

A passing result supports **disposable/synthetic-worker readiness only**.
It is not a performance guarantee or an independent security audit. In
particular, the 160 MiB allocation approximates one hypothetical large
response while real provider libraries, concurrent SDK calls, TLS buffers,
model payloads and production traffic patterns can behave differently.
The actual 512 MiB Render Cron instance may have different cgroup overhead
and timeouts than Ubuntu GitHub Actions. No real external requests, live
customer data, paid AI traffic, network timeouts, production database rows
or tickets are used here.

Stop the release if any scenario fails, if child RSS reaches 384 MiB, if a
retry or cancellation produces a false completion, if a provider call occurs,
if required jobs are skipped, or if the Cron canary ceases to be suspended.

Before **any** actual Render worker can be enabled:
- qualified independent security review of its exact source and secrets;
- explicitly approved disposable *Render-hosted* realistic simulated
  workload/canary if additional memory/runtime comparison is desired;
- verified production-owned environment and planned limited shadow with
  safe PostgreSQL claims, bounded costs and rollbacks;
- distinct owner authorization for merging a PR, changing GitHub schedule,
  adding production credentials, executing real jobs or privatizing the repo.

The existing GitHub production worker remains untouched. Phase 1Y ticket
writes stay disabled. PRs #44, #45 and this stress-test draft remain unmerged.
