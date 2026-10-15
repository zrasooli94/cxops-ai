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

---

# Independent security review - second reviewer (PR #33, issue #34)

Reviewed commit: `9ef585bcbb168583bc4e032d998baa1fdb693687`. Method: full
diff plus the surrounding code (route, signature verifier, enqueue,
worker handler, downstream `ZendeskWebhookService`), then an adversarial test
suite over the real HTTP route against disposable PostgreSQL with synthetic
tenants and secrets. Labels: **VERIFIED** (executed), **INFERRED** (reasoned),
**NOT TESTED**.

## Verdict: CONDITIONAL PASS (after the fixes in this review)

The exact-signed-delivery defense is correct for the threat it names and the
unique partial index closes the concurrency window. The PR as submitted had
**three defects reachable without breaking the signature scheme** and one
downstream cross-tenant weakness; all are fixed and pinned by tests below.
Merge is conditional on the owner-approved migration sequencing (section 6).

## Findings

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | MEDIUM | `hmac.compare_digest(str, str)` raises `TypeError` for a non-ASCII signature header, so **an unauthenticated request with a current timestamp got HTTP 500** (VERIFIED, 3 cases). Pre-existing, but on the PR's path and on its checklist. | Fixed: bytes comparison, malformed header => 401 |
| 2 | MEDIUM | Cross-tenant `invocation_id` collision: `dedupe_key` is globally unique and the invocation header is **unsigned and sender-chosen**, so tenant B's delivery carrying a value already used by tenant A raised `ValueError` => **500** (and a concurrent variant surfaced a raw `UniqueViolationError`). No data was exposed (the PR's org check prevented that) but a 500 makes Zendesk retry, and a tenant with a valid secret could squat another tenant's key. VERIFIED. | Fixed: key scoped by the trusted organization (`zendesk:{org}:{invocation}`); legacy raw keys honoured only for the same tenant and job type; collisions can no longer occur |
| 3 | MEDIUM | Invocation id unbounded and unvalidated: > 255 chars => DB `StringDataRightTruncation` => 500; arbitrary text reached log lines and unique keys (log-injection surface). VERIFIED. | Fixed: `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`, else 400, before any work or logging |
| 4 | MEDIUM | Request body fully buffered **before** authentication with no limit (memory DoS by anyone who knows an integration id). VERIFIED (2 MiB accepted). | Fixed: `ZENDESK_WEBHOOK_MAX_BODY_BYTES` (default 256 KiB), enforced on `Content-Length` and while streaming => 413 |
| 5 | MEDIUM | `ZendeskWebhookService.process` looked up `TicketEvent.event_key` **globally** and reused the row: a foreign tenant's event was returned as "already processed" (suppression) or reused. VERIFIED. | Fixed: an event whose ticket is not owned by the job's tenant raises `PermanentJobError` (fail closed) |
| 6 | LOW | Fingerprint domain separator was the two characters `\0` (backslash, zero) instead of NUL. No security impact (still a fixed prefix). | Fixed: real `\x00` |
| 7 | LOW | The 404 (unknown id) vs 401 (known id) distinction reveals existence, contrary to the docstring. Integration ids are UUID4 (122 bits), so enumeration is infeasible. | Accepted, documented |
| 8 | LOW | No request-rate limit on the webhook route. Bounded by body cap and signature check; an edge limit is advisable. | Documented |

## Answers to the checklist

1. **Exact authenticated bytes / timestamp / bound integration / tenant /
   unsigned invocation id** - VERIFIED. The fingerprint is
   `SHA-256(domain || len||integration_id || len||timestamp || raw_body)`;
   the HMAC covers `timestamp + body` exactly as sent; changing the unsigned
   invocation header cannot change the fingerprint. A body signed for tenant A
   posted to tenant B is 401. Replay window is enforced in both directions and
   a non-positive window cannot be configured.
2. **Unrelated genuine events with identical timestamp + body** - INFERRED
   low. The accepted payload shape (`type`, `detail.id`) is Zendesk's event
   envelope, which carries a unique event `id`/`time`, so identical bytes mean
   the same event. A custom static trigger body could collide only within the
   same signing second and would then trigger the same automation twice with
   an identical effect; collapsing it is the safer outcome. No extra key is
   required for release.
3. **Replay with a newly signed timestamp** - VERIFIED limitation, by design:
   a re-signed delivery (new timestamp) is a new fingerprint and is processed
   again. Only a holder of the secret (or Zendesk itself) can produce it. The
   downstream layer is the second line: `TicketEvent.event_key` and the
   `CXOps Event: <id>` private-note marker de-duplicate per invocation id, but
   NOT across different invocation ids. Event-level de-duplication (hashing
   the payload's event `id`) would need another unique key and migration and
   is recommended as a follow-up, not a release blocker.
4. **Concurrency / unique partial index / old jobs / 202** - VERIFIED: 10
   concurrent relabelled copies produce one job; 4+4 concurrent cross-tenant
   collisions produce one job per tenant; duplicates return 202 with the
   original job id; legacy raw-key jobs of the same tenant still de-duplicate
   retries that straddle the deploy.
5. **Rotation, malformed headers, stale/future timestamps, writeback
   idempotency** - VERIFIED: after rotation the old secret and its captured
   deliveries are 401 (so a stale fingerprint cannot be abused), a new-secret
   delivery is accepted; 11 malformed timestamps (empty, `0`, negative, huge,
   Unicode digits, NaN, invalid ISO, SQL text) are 4xx, never 500. Rotation has
   no dual-secret grace window: deliveries in flight during a rotation are
   rejected and retried by Zendesk (availability, not security). Writeback is
   idempotent per invocation id by the marker scan; see item 3 for the
   cross-invocation case.
6. **Migration sequencing** - INFERRED/READ: `1u0h0001` adds a nullable column
   and a partial unique index (expand-only, rollback = drop). The startup schema
   gate refuses a build whose schema is behind (the previous deploy keeps
   serving), and the scheduled worker has its own gate. The index is built
   without `CONCURRENTLY`, taking a SHARE lock on `integration_jobs` for the
   build; at pilot table size this is sub-second but should be done in a quiet
   window. Required order: **apply migration, then merge/deploy**. Old code
   keeps working against the new schema. A rollback to the old revision loses
   the fingerprint check and the scoped keys (it would process one duplicate
   for a retry that straddles the rollback). Production migration was NOT
   applied or approved here.
7. **Colliding invocation id => 500 / data exposure** - fixed (finding 2).
   No response ever carries another tenant's job id or status (VERIFIED).
8. **CI and data** - synthetic only; no Zendesk call, secret, production data,
   notification, paid infrastructure or deployment.

## Tests added (`tests/test_phase1uh_security_review.py`)

Relabelled replay; re-signed delivery is a distinct job (limitation pinned);
cross-tenant replay of a signed delivery; cross-tenant and concurrent
invocation collisions; legacy-key handling for another and for the same
tenant; non-ASCII and oversized signatures; 11 malformed timestamps; 7 invalid
invocation ids; oversized body; secret rotation; fingerprint uniqueness and
non-exposure; foreign `TicketEvent` never reused. One existing test
(`test_duplicate_invocation_is_deduped`) now asserts the tenant-scoped key.

## Remaining risks

| Sev | Risk |
|---|---|
| MEDIUM | No event-level de-duplication across re-signed deliveries with new invocation ids (follow-up). |
| LOW | No edge rate limit on the public webhook route. |
| LOW | Non-concurrent index build; no dual-secret rotation window. |
| LOW | `TicketEvent.event_key` remains globally unique (cross-tenant processing is blocked, but a sender-chosen key can still make another tenant's identical key fail permanently; unpredictable UUIDs make this impractical). |
