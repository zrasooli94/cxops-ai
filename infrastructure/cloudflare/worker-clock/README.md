# CXOps AI – Cloudflare clock bridge (OFF by default)

This is a **source-only protocol prototype**, not a deployed worker and **not a
replacement executor**. Existing GitHub Actions production scheduling remains
unchanged. The Python one-shot worker is still the only deployed executor.

The proposed transport is:

```
Cloudflare Workers Free Cron (every 5 min)
  -> pinned HTTPS POST + HMAC-SHA256 + signed slot + nonce
  -> disabled FastAPI receiver (source only)
  -> atomic PostgreSQL slot ledger + bounded catch-up (source only)
  -> disabled globally leased Python executor (source only; no invoker)
```

## Safety controls already implemented

- `wrangler.jsonc` defines **zero Cron Triggers** and
  `CXOPS_CLOCK_DISPATCH_ENABLED="false"`. Do not deploy or enable without a
  separate owner-approved operational and security plan.
- Exactly one fixed HTTPS destination is baked into source. No caller or
  environment-supplied destination, URL redirects, or public HTTP control
  endpoint is accepted. No worker data, customers, database credentials,
  OpenAI keys, or Zendesk credentials are sent in the signed body.
- HMAC signature binds POST method, exact path, UTC scheduled slot,
  nonce and SHA-256 of the JSON payload. Timestamp and nonce use strict
  validation and a 15-minute maximum age. Separate Cloudflare and Render
  secrets must be configured out-of-band; do not place either in GitHub.
- The standalone Python verifier rejects malformed requests, forgeries,
  untrusted JSON, duplicate header case variants and out-of-window commands.
- **Critical:** HMAC by itself does not prevent replay inside its time window.
  The disabled receiver therefore commits a unique `(clock_id, slot)` row. A
  duplicate receives the same acknowledgement without creating another row.
- Key rotation is bounded to one configured current key and one configured
  previous key with an explicit expiry. The signed key identifier prevents
  attacker-controlled lookup.
- The future API route must be **OFF by default**, inaccessible in staging
  and production until separately reviewed, refuse unexpected host/origin,
  require TLS, and check exact routing query/header duplication.
- Keep a separate observer for stale ticks and worker backlog. Cron is
  best-effort scheduling and not an exactly-once delivery SLA.

## Free-first economics (not a deployment quote)

At five-minute intervals, there are 288 theoretical ticks/day and 8,640
in 30 days. This fits well below Workers Free's daily request allowance,
subject to any account-level Cloudflare usage and CPU limits. The clock
bridge is lightweight: **it cannot run the existing Python worker inside
Cloudflare Free**.

Current GitHub Actions workflow takes roughly 1 minute per successful
run including setup. A theoretical 8,640 one-minute runs/month alone exceed
GitHub Free's included 2,000 private-repository Actions minutes by 6,640,
excluding CI (actual scheduled GitHub runs are frequently delayed or
omitted). This is why merely switching GitHub to private is risky.

The existing paid Render API is already `0.5c-512mb` (~$7/month listed
Starter plan) and is NOT being upgraded here. A future bounded worker
executor co-located in that service might avoid new compute spend, but its
512 MB memory, CPU impact, process lifetime and restart behavior must be
load-tested independently. An isolated additional Render background worker
requires **new paid spend** and is not authorized. Free Render web instances
sleep, so a scheduler that pings staging every five minutes could exhaust
free instance-hour allocations: do not deploy this to the existing Free
staging services as a keepalive.

## Migration gates (all require new authorization)

1. Confirm source-only test and independent security review of the clock
   transport, signing protocol and future executor design.
2. Confirm Cloudflare account permits Workers Free Cron and remains under
   existing account's limits. Do not create paid plans or accounts.
3. Independently review the disabled receiver, additive migration and executor;
   repeat concurrency/replay/delay and fail-closed tests before giving them any
   credential or deployment invoker.
4. Decide where the Python worker actually executes: preferably a dedicated
   reliable worker after commercial budget approval, or a reviewed co-located
   short-run executor **only if** Render CPU/memory capacity tests pass.
5. Run safe read-only schedule and health probes against a dedicated isolated
   test target without exposing or waking production. Measure scheduling
   lateness, missed ticks, restart recovery and backlog to establish SLO.
6. Independently approve the exact version and funding. Configure the
   Cloudflare Cron trigger and secret bindings; enable the receiver on a
   synthetic canary only. Keep GitHub production worker running as primary
   during canary but prevent duplicate operations via database job fencing.
7. With owner authorization, move dispatch in a staged migration, verify
   no notification/integration duplication, and monitor safety for 24+ hours.
8. Only after a reliable cutover, change/disable the GitHub production Cron
   and switch GitHub private. Verify Render/Vercel private-repo access and
   private-compatible CI first. Never make the cutover in a draft-only task.

## Rollback

Turn the new receiver feature flag **OFF**, disable/remove Cloudflare's
Cron trigger, and keep or resume the existing GitHub Action schedule.
No DB rollback should be necessary: rejected/duplicate slots remain durable
and jobs preserve their own row-lock/claim fences. If the new executor is
unhealthy, block new dispatch but retain every queued job for the approved
fallback. Do not automatically republish a private GitHub repository.

## Run locally (no credentials and no network)

```bash
node --test infrastructure/cloudflare/worker-clock/index.test.mjs
PYTHONPATH=. python -m unittest discover -s tests -p 'test_worker_clock_protocol.py' -v
```

**Never run Wrangler deploy from this milestone. No production endpoint exists.**
