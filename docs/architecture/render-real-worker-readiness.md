# CXOps AI — Real Python worker disposable readiness

**Status: source-only / synthetic PostgreSQL / not authorized for production.**
This is the next step after the completed, manually suspended 24-hour inert
Render Cron canary. Tracks [Issue #40](https://github.com/zrasooli94/cxops-ai/issues/40)
and depends on draft [PR #44](https://github.com/zrasooli94/cxops-ai/pull/44)
for the selected bounded Render native Cron launcher.

## Established, limited evidence

- Isolated Render native Cron no-op: 288/288 expected five-minute starts,
  zero missing/duplicates/failures, 288 valid synthetic log events and maximum
  process RSS 21.543 MiB during the exact 24-hour observation period.
- Synthetic Cron service \`crn-dbauc361egvs73c65pgg\` was user-suspended on
  2026-10-20 11:15:16 UTC. It must **remain suspended** in this milestone.
- The no-op canary does **not** prove actual CXOps worker memory, outbound
  provider latency, schema/claim correctness or provider load handling.
- Source-only PR #44 provides a fail-closed real worker entrypoint:
  \`sh scripts/run_render_cron_worker.sh\` runs production environment preflight
  before \`scripts.worker --once --max-jobs 25 --max-seconds 240
  --exit-nonzero-on-unsuccessful --finalize-expired-public-chat
  --expiry-batch-size 50\`. This proposed command is **never invoked with
  production credentials** by the disposable test.

## Disposable execution security boundary

This PR adds a separate, mandatory GitHub Actions suite:
\`.github/workflows/render-real-worker-disposable.yml\`.
It runs only when explicitly triggered by a PR or manual CI dispatch:
**NO cron, no Render/Cloudflare deployment, no provider credentials, no
external application endpoints**.

The suite provisions the \`pgvector/pgvector:pg16\` service on loopback using
the exact throwaway credentials \`cxops_probe / cxops_probe\` and DB name
\`cxops_worker_probe_disposable\`. The stdlib-only
\`scripts/worker_disposable_guard.py\` requires an explicit opt-in,
\`GITHUB_ACTIONS=true\`, \`ENVIRONMENT=development\`, \`DEBUG=false\`,
\`AUTH_MODE=hs256\`, and this exact loopback DSN. Nonempty LLM, Zendesk,
Neon/Nhost, cloud provider, webhooks, encryption and other credential
variables fail before migration or import. No production database credentials
are read, copied or passed through.

The checked-in probe runs actual Alembic migrations **only against the
disposable local database** and then:

1. Executes the **real** \`python -m scripts.worker --once\` process once with
   its normal SLA scan and expired-chat cycle against **empty local tables**.
   Measures child-process elapsed time and peak RSS. Asserts no integration
   jobs, organizations, tickets or chat sessions were changed.
2. Exercises the **actual integration-job repository** (\`SELECT ... FOR UPDATE
   SKIP LOCKED\`, commits, fenced terminal writes) concurrently from two
   one-shot worker executions, with strictly synthetic rows in two throwaway
   tenants. Only \`IntegrationJobService.execute\` is replaced with an
   in-memory no-provider stub. Checks every synthetic job completes exactly
   once and tenant ID bindings are preserved.
3. Verifies transient retry, nonretryable terminal failure, recovery after an
   abandoned processing claim, poisoned exhausted claim and rejection of a
   stale completion token on actual PostgreSQL.
4. Tests the **actual PostgreSQL advisory scanner lock** with two concurrent
   contenders while stubbing only the scanner body, proving the second scan
   cannot run in parallel.
5. Runs the existing extensive worker one-shot and expired-chat lifecycle
   regression suites, including real disposable tenant/chat rows, fairness
   and concurrency, plus the original production-worker workflow safety tests
   and guard negative cases.

The local database and temporary organizations/jobs exist only inside
ephemeral GitHub runners; test cleanup deletes synthetic rows and the GitHub
service container is destroyed at job exit. The test has no Render service or
Neon database binding.

## Interpretation and release criteria

Even if these checks pass, the result is **source-only disposable-worker
readiness**, not authorization to merge, provision, unsuspend the synthetic
Cron, or consume real customer/integration work. The real subprocess is tested
on an empty queue, whereas concurrent processing uses a stub for all external
job effects. In particular, this test does NOT establish:

- 0.5 CPU/512 MiB headroom under **provider-heavy real workload**;
- end-to-end Zendesk/OpenAI/notification behavior or provider timeouts/cost;
- a safe shadow test alongside the existing production GitHub worker;
- 7-day scheduling/queue-age SLO with real customers;
- worker cancellation/forced termination at the Render platform boundary;
- a qualified, independently performed human application-security approval.

Do not extrapolate subprocess RSS to all integrations. If the measured empty
worker peak is **>= 384 MiB** (25% reserve under 512 MiB) or the test fails,
the result is **NO-GO** even for additional workload tests. Passing an empty
worker probe only justifies a later, separately approved **representative
synthetic workload** with disposable PostgreSQL and zero live providers.

## Outstanding gates (separate owner decisions)

1. Review exact source diff and GitHub CI on this disposable worker PR.
2. Develop a representative, offline provider-*simulated* workload with
   bounded fake latency, retry storms, memory pressure and cancellation
   behavior; instrument peak RSS, DB pool contention and job queue latency.
   Do not send real provider requests.
3. Obtain qualified, independent security signoff for actual production-acting
   code and proposed Render Cron secrets/cutover. The brother's earlier
   limited \`COMMENTED\` review is accepted only as owner risk acceptance for
   inert synthetic preparation.
4. Separately authorize any production configuration/secrets, actual
   Render worker activation or migration of the existing GitHub scheduler.
   Existing GitHub worker remains unchanged. The previously approved
   $2/month budget target and $6 escalation threshold are **not a hard cap**.
5. Observe a later authorized overlap/shadow phase and verify rollback
   before making the code private or commercial traffic live.

**Never start, resume or edit the suspended synthetic Render Cron service
while conducting these tests.** Preserve its logged evidence and only archive
or delete it with a distinct owner instruction.
