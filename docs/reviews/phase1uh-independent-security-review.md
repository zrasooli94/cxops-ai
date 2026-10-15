# Phase 1U-H independent security review checklist (draft, 2026-10-15)

## Threat boundary

Verified Base64 HMAC authenticates timestamp + raw body, *not* the separately
supplied invocation ID. The new SHA-256 fingerprint includes the trusted
integration path ID and exact authenticated bytes. The partial unique
PostgreSQL index arbitrates simultaneous insertions, independently of worker
count. The original invocation-ID job key and retry/writeback marker are
unchanged for first deliveries.

## Precise guarantee, and what it does NOT guarantee

- Exact signed delivery replay under another invocation ID produces the same
  fingerprint *for the same trusted integration*, so a retained job prevents
  a second enqueue. A unique database index is required; application-only
  prechecks are not sufficient under concurrency.
- It is **not** general event idempotency: a sender that re-signs identical
  payload bytes with a distinct timestamp generates a distinct fingerprint.
  Neither an unsigned ID nor plain body hash identifies an immutable event.
- Retention is currently the lifetime of the original integration job.
  Deleting historical jobs also deletes the replay record. Expired timestamps
  cannot pass the verifier, but clock skew and retention must be evaluated.
- Identical timestamp+body bytes can describe separate legitimate deliveries
  and would be collapsed. Confirm Zendesk delivery/retry guarantees before
  considering release. Without a signed immutable event ID, exact replay
  detection cannot distinguish intentional duplicates from literal replays.
- Secret rotation does not change the fingerprint if timestamp/body remain
  identical (and the current secret authenticates both); a fresh timestamp
  creates a fresh identity. Old-secret signatures stop verifying after rotation.

## Deployment compatibility warning

The new ORM column is read by SQLAlchemy queries even when null. Deploying
application code *before* the expand-only migration exists will break job
queries. The existing startup schema gate must enforce head `1u0h0001`
before the updated application is served. The migration has NOT been
approved for or applied to production. Do not merge/deploy this draft.

## Test evidence status

- Synthetic ASGI ingress reproduction and fingerprint equality.
- Disposable PostgreSQL sequential replay and independent-delivery checks.
- Concurrent two-session insertion test using a unique fingerprint.
- Existing generic and Zendesk authentication regression inventory.
- Explicitly pending: a fully end-to-end worker/writeback test, independent
  review of real legitimate-identical-delivery semantics, full green latest
  CI and production migration release sequencing review.

## Review verdict

NOT CERTIFIED / DRAFT. Proceed only after the CI gates and independent
review of semantics and migration sequencing. No provider requests, secrets,
production data, deployment, or notification activation are authorized here.
