# Reliable worker clock — source-only design

Status: **disabled prototype; not deployed; not production-approved**.

## Architecture and safety boundary

The proposed clock is Cloudflare Workers Free Cron every five minutes. It sends
one pinned HTTPS request containing only protocol version and UTC slot. The
request is HMAC-SHA256 authenticated over method, exact path, slot timestamp,
UUID nonce, bounded key identifier and raw body hash. The current and one
explicit previous key can overlap only until its configured aware expiry;
arbitrary key lookup is not allowed.

The FastAPI receiver is present in source but returns 404 unless
`WORKER_CLOCK_RECEIVER_ENABLED=true`. It rejects duplicate raw security headers,
query strings, alternate paths, stale/future slots, oversized or non-JSON bodies,
unknown keys and invalid signatures before writing. A valid delivery and its
retry both return the same 202 response after the transaction commits.

The PostgreSQL `worker_clock_ticks` table is the replay fence. Unique
`(clock_id, slot)` is authoritative under concurrency. When a later valid tick
reveals a gap, the receiver inserts only a configured bounded tail (maximum 288
slots) and reports truncation in bounded logs. No cleanup exists in this
milestone: terminal slot keys therefore cannot be deleted inside the signature,
claim-recovery or audit window, and pending/processing rows are never expired.

The source-only Python executor is also independently disabled. It holds one
session-level PostgreSQL advisory lock for the entire worker pass, coalesces all
older pending ticks, claims the newest with a UUID fencing token, and then calls
the existing bounded `run_worker_once`. A second process returns `busy`; catch-up
cannot create parallel 240-second drains. Crash releases the database lease, and
600-second stale recovery exceeds the current six-minute platform backstop.
The entry point runs the existing production environment preflight and schema
gate before claiming anything.

**Missing operational component:** neither the receiver nor this PR invokes the
executor. Cloudflare can durably record intent but cannot launch a Render shell
command. A reliable executor process/topology must be approved separately.

## Evidence and capacity assessment

On 2026-10-18, the latest 50 GitHub schedule-created runs produced 49 intervals:
minimum 107.70 minutes, median 249.90, maximum 584.55; all 50 completed
successfully. That confirms successful execution does not imply five-minute
scheduling reliability. GitHub also documents that scheduled events may be
delayed or dropped under load.

The existing Render production API was read-only verified as one Singapore
`0.5c-512mb` instance. An empty synthetic worker pass against disposable
PostgreSQL completed its worker body in 0.068 seconds (0.65 seconds process wall
time) with approximately 105 MB maximum RSS locally. This is only a lower bound:
it does not measure the API process plus a real job, provider SDK buffers,
concurrent HTTP traffic, or a worst-case in-flight job. Therefore the existing
512 MB API is **not yet proven sufficient** for co-located execution. Do not add
an API lifespan consumer or run the executor there until a synthetic combined
HTTP/worker load test establishes memory headroom, latency, restart behavior and
an enforceable concurrency budget.

## Cost comparison (USD, 30-day estimate)

| Option | Incremental monthly cost | Reliability and rollback |
| --- | ---: | --- |
| Cloudflare Workers Free clock | $0 | 288 ticks/day is far below 100,000 free requests/day. Five-minute Cron has a 10 ms CPU limit and one outbound request fits the design, but cadence must be observed for 24+ hours on an approved isolated target. Disable the trigger and receiver to roll back. |
| Existing Render API executor | $0 incremental; existing API is about $7/month | No extra service, but 0.5 CPU/512 MB capacity and process-lifecycle safety are unproven. Roll back by disabling executor and receiver flags. Not recommended without load proof. |
| Dedicated Render background worker `0.5c-512mb` | about $7/month additional | Clean process isolation and direct executor lifecycle. Best reliability of the Render choices, but it is a new paid service and is not authorized. Stop/disable it and retain ledger rows to roll back. |
| GitHub Actions, public repository | $0 | Current measured cadence is far outside five minutes; keep only as fallback until canary proves replacement. |
| GitHub Actions, private Free | roughly $39.84/month overage at one Linux minute per tick, before CI | 8,640 monthly ticks minus 2,000 included minutes = 6,640 minutes at $0.006/minute. Billing or usage blocking does not fix schedule delays. |

Sources: [Cloudflare Workers limits](https://developers.cloudflare.com/workers/platform/limits/),
[Cloudflare pricing](https://developers.cloudflare.com/workers/platform/pricing/),
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[GitHub schedule reliability](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows),
[Render compute plan mapping](https://render.com/docs/compute-plans-update), and
[Render 0.5 CPU/512 MB pricing example](https://render.com/articles/production-rails-hosting-guide).

## Required canary and rollback gates

1. Obtain independent human security approval of the exact receiver, migration,
   signing keys, and executor deployment topology.
2. Choose and explicitly authorize the invoker: a dedicated Render worker is the
   current safer recommendation; co-located API execution needs load proof first.
3. Provision only an isolated synthetic target. Keep Cloudflare Cron absent and
   both feature flags OFF until that separate authorization.
4. Run 24+ hours of cadence observation. Measure tick lateness, duplicates,
   truncated gaps, pending-ledger age, global-lease contention, worker duration,
   queue age, API p95 latency, memory high-water mark, OOM/restart count and DB
   pool saturation. Initial acceptance target: 99% of ticks received within 10
   minutes, no unrecovered gap over 15 minutes, one executor globally, and no API
   SLO regression. Final SLO must be approved from observed data.
5. Canary with the GitHub worker still primary. The database job queue and clock
   lease must prevent concurrent processing; do not enable Phase 1Y ticket writes.
6. Roll back by disabling executor, then receiver, then Cloudflare trigger. Keep
   ledger and queued jobs. Resume/retain GitHub Actions until the rollback is
   verified; no migration downgrade is required.

No production database, deployment, provider, credential, paid resource,
repository visibility or existing worker workflow is changed by this PR.
