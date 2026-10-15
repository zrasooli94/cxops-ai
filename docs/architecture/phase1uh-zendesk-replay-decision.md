# Phase 1U-H — Zendesk replay protection: verified boundary and decision gate

Status: **reproduction-only draft**, not a remediation. No runtime change, migration,
production operation, provider call or notification activation.

## Observed source behavior (master c96579bc)

1. `receive_zendesk_ticket_webhook` resolves an integration/tenant and verifies
   Base64 HMAC-SHA256 over exact timestamp header bytes + raw HTTP body.
2. Invocation ID is mandatory but is not signed.
3. `enqueue_zendesk_event` looks up `IntegrationJob.dedupe_key` equal
   to invocation ID, then inserts an IntegrationJob with unique dedupe_key.
4. Changing only invocation ID leaves signature valid and yields another unique
   dedupe_key, which can result in two separately claimable jobs.
5. `ZendeskWebhookService.process` also uses invocation ID for TicketEvent
   event_key and the Zendesk writeback marker. Two IDs can therefore bypass
   that downstream marker-based idempotency check.

ASGI reproducer: `tests/test_phase1uh_zendesk_replay_reproduction.py`.
It mocks enqueue and connection lookup, so it proves the acceptance boundary
but **does not** prove durable double-insert or provider-side duplication.

## Alternative evaluation

| Design | Concurrent duplicate safety | Retry behavior | Costs / risks |
| --- | --- | --- | --- |
| Hash timestamp + raw body as the sole job key | Unique index prevents exact signed replay | Same invocation with a re-signed timestamp can create a second job | No migration, but breaks historic job keys and cross-attempt retry semantics |
| Hash body + tenant as job key indefinitely | Unique index prevents same-body replay | Suppresses legitimately repeated identical events for all time | Unsafe without trustworthy provider event identity and bounded retention |
| Timestamp buckets plus body digest | Unique index within a bucket | Boundary-crossing retries can escape; identical legitimate events collide | Misleading replay guarantee |
| In-memory recent hash cache | Single-process reduction only | May forget retries on restart | Multi-instance race; insufficient |
| Durable tenant-scoped replay ledger with unique fingerprint and expiry | Strong across workers if inserted atomically with job | Must define distinct delivery/event semantics and expiry carefully | Migration, retention/cleanup, Neon write/storage budget |

## Release blockers before implementing behavior

- Verify provider retry rules and whether a stable **signed** event identity
  is available. A signed timestamp is not a unique event identifier.
- Reproduce two durable jobs and downstream handling in disposable PostgreSQL,
  including concurrent requests and worker claim racing.
- Validate legitimate identical-payload events, retries with newly signed
  timestamps, old historic invocation-key jobs, malformed IDs, cross-tenant
  identical payloads and secret rotation.
- Select an atomic uniqueness boundary **before** calling the fix replay-safe.
  If a new ledger is required, propose a reviewed migration and bounded
  cleanup cost rather than silently changing production schema.
- Full backend tests, Schemathesis, lint/typecheck, frontend, Docker and secret
  scan must be green; independent review required before changing runtime.
- Leave notifications OFF, production secrets/data untouched, no real Zendesk
  calls, and do not merge/deploy this security change without review.

## Status of this draft

- Actual repo source inspected; model and service uniqueness reviewed.
- Synthetic ASGI reproducer committed but not independently executed here.
- No disposable PostgreSQL test executed, no full suite or CI result claimed.
- No security remediation or production change delivered.
