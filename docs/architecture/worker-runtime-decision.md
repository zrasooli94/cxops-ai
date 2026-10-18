# Production worker runtime decision

Status: source-only recommendation; no service, schedule, environment variable,
secret, migration, or production worker has been changed.

## Decision

Use one Render native Cron service in Singapore on `0.5c-512mb`, scheduled for
`*/5 * * * *`, running `sh scripts/run_render_cron_worker.sh`. Keep the current
GitHub Actions worker unchanged as the primary until a synthetic canary and a
shadow production observation window pass. Do not deploy the Cloudflare clock
or a continuously running background worker for the initial migration.

This is the least expensive topology that matches the required five-minute
periodic workload. It also removes a public receiver and a second scheduling
provider from the critical path. The existing durable PostgreSQL queue, atomic
`FOR UPDATE SKIP LOCKED` claim, stale-claim recovery, SLA advisory lock, bounded
one-shot execution, and expiry batch remain the data-safety authority.

## Evidence and cost model

- Current production service inventory: one `0.5c-512mb` Render API instance in
  Singapore. No production background worker or Cron service exists.
- Latest 56 successful scheduled GitHub runs: 34s minimum, 55s median, 78s
  p95, 86s maximum, and 57.04s mean end-to-end runner duration.
- Disposable PostgreSQL empty pass: 0.50s process wall time, 0.07s worker body,
  and 106,381,312-byte maximum RSS (about 101.5 MiB). This is a lower bound;
  provider-heavy jobs can use more memory and still have the 240-second bound.
- Render prices `0.5c-512mb` at $7 per continuously active month and Cron by
  active second, with a $1 monthly minimum. At 8,640 runs and the observed
  57.04s mean, the conservative estimate is 136.9 active hours or $1.31/month.
  At the 78s p95 it is $1.79/month; at the 240s application bound it is
  $5.52/month. A full-time background worker is $7/month.
- Build pipeline and outbound bandwidth are expected to remain within the
  existing Hobby allowances, but are usage-dependent and not guaranteed $0.

## Architecture comparison

| Property | Render native Cron | Cloudflare Cron + Render worker | Render worker + internal timer |
| --- | --- | --- | --- |
| Incremental compute | $1 minimum; expected $1.31, p95 model $1.79 | $7 worker; Cloudflare Free $0 | $7 worker |
| Scheduling path | Render schedule -> bounded process | Cloudflare -> public HMAC receiver -> ledger -> worker | resident process loop |
| Startup latency | cold process/container; must be measured in canary | worker already warm; HTTP clock latency added | warm after deploy/restart |
| Overlap | platform guarantees one run; DB claims remain authoritative | ledger dedupe plus worker lease/DB claims | one instance initially; DB claims remain authoritative |
| Missed work | durable queue waits for next run/manual trigger | slot ledger records bounded catch-up | queue polling resumes after restart |
| Memory isolation | separate 512 MiB only while active | dedicated 512 MiB continuously | dedicated 512 MiB continuously |
| Recovery | next tick, manual run, or temporary GitHub fallback | receiver retry, ledger recovery, worker restart | automatic restart plus durable queue |
| Complexity | lowest: one service and one command | highest: two providers, receiver, HMAC rotation, ledger, worker | medium: timer, shutdown and housekeeping lifecycle |

Render documents a single-run guarantee, but does not publish a scheduled-start
latency SLA. A canary must therefore measure actual scheduled time to process
start and alert when it exceeds the agreed threshold. Cloudflare Free is a
useful future independent clock if Render Cron cadence proves unacceptable; it
is not useful redundancy while execution still depends on the same Render
region and database.

The continuously running worker gives approximately one-second queue polling,
but the current continuous loop does not run the bounded public-chat expiry
cycle. Making it functionally equivalent would add internal scheduling and
graceful-shutdown work. Pay the extra fixed $5.69-$6/month only when commercial
traffic requires sub-five-minute integration-job latency or Cron consistently
misses its start SLO.

## Staged deployment plan (requires explicit approvals)

1. Human security review this branch and draft PR #41. Approve a monthly cap of
   $2 initially, with an emergency ceiling of $6 for bounded overruns.
2. Provision one isolated Render Cron canary with synthetic database credentials
   and no external-provider credentials. Run for at least 24 hours; require no
   overlap, no missed slot, p95 start delay <=60s, p95 RSS <384 MiB, and zero
   unsuccessful runs.
3. Approve production secret sharing through a Render environment group and the
   additive ledger migration separately. Keep all ticket-write flags off.
4. Start a seven-day production shadow: Cron runs with the GitHub worker still
   enabled. Durable claims make overlap safe; compare queue age, start delay,
   runtime, RSS, failures, and capacity exhaustion.
5. If gates pass, disable only the GitHub schedule (retain manual dispatch) and
   observe seven more days. Cloudflare receiver/executor flags remain off.

## Rollback

1. Disable the Render Cron schedule or suspend its service; do not cancel an
   in-flight run unless it is confirmed stuck.
2. Re-enable the unchanged GitHub schedule and use manual dispatch once if queue
   age requires it. Durable PostgreSQL claims prevent double execution.
3. Do not drop `worker_clock_ticks` during incident rollback. It is inert while
   receiver/executor flags are off and preserves audit evidence.
4. If a run was terminated, wait for the existing stale-claim recovery window,
   then confirm pending/retry/processing counts before the manual drain.
5. Roll the Cron service back to its previous successful commit or suspend it;
   no API rollback is required because the Cron process is isolated.

## Approval boundary

Provisioning the Cron service, adding its schedule, moving or sharing secrets,
running production migrations, enabling any receiver/executor flag, changing the
GitHub production workflow, or performing a production drain all require
explicit owner authorization. Formal human security approval is still required.
