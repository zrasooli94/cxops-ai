# Phase 1X — Read-only staged Control Center

**Status:** Draft implementation, verify latest CI and visible preview before release.

## Why this exists

The first verified staging view proved authentication only. Phase 1X lets an
enrolled synthetic tester inspect **real tenant-authorized read-only data** from
the isolated Neon staging database, with an actionable ticket/customer overview
instead of a page of hard-coded totals.

## Data changes — isolated staging ONLY

Source: Neon project `rough-hall-90121003`, staging branch
`br-spring-truth-azv32ox5`, database `cxops_staging`, not a production clone.

Using one idempotent transaction, created synthetic fixtures:
- Alpha Workshop (organization 1): 3 customers and 6 tickets
  (open, pending, new and solved statuses; urgent/high/normal/low priorities).
- Beta Workshop (organization 2): 2 customers and 2 tickets.
- Every fixture has an `external_id` beginning `PHASE1X-`, customer
  names beginning `Synthetic `, fictional `example.test` addresses.
- Every ticket has `source=synthetic_staging`; descriptions explicitly
  identify fictional requests. No integration jobs, AI requests, outbound
  notifications, provider actions, customer imports, passwords, real domains,
  or production mutations were made.
- Idempotence: transaction uses `ON CONFLICT (organization_id,external_id)
  DO NOTHING`; these keys are reserved for this synthetic exercise only.

The frontend rejects ANY response containing a record from a different
organization, lacking a synthetic identifier or wrong `source`, with an
explicit unavailable error. It never silently displays foreign records.

## Read-only API additions

The initial staging HTTP allowlist now includes GET/HEAD `/tickets` and
`/customers`, with all existing backend capabilities and tenant membership
requirements preserved. POST/PATCH/DELETE remain blocked by staging middleware.
Existing `/staff/public-chat/summary?window=24h` supplies true queue counts
(and correctly displays zero if no handoff sessions exist).

## Frontend behavior

- `/staging/operations` is a dynamic server-rendered staging-only route.
- Only a verified, enrolled Neon session receives a server-side auth token.
- Exactly one backend-verified synthetic organization must be returned by
  `/me/organizations` before any ticket, customer or queue calls.
- All subsequent requests use the *server-determined* organization selector.
- Never accept a user-provided organization ID, access token or target URL.
- Separate API responses are validated and no-store. If a request fails or
  contains non-synthetic data, render an unavailable state (never zero).
- No client-side secrets; no sign-in to Nhost; no data-changing UI.
- A clear banner identifies data as synthetic and the system as read-only.

## Test and release gates

- Existing staging provider/worker disablement and exact-schema checks.
- New unit tests for organization ambiguity, cross-tenant ticket/customer
  records, synthetic markers, handoff summary and ticket counts.
- Docker, backend unit tests, Schemathesis, frontend build/typecheck, secret
  scan and staging safety tests all pass on current HEAD before calling ready.
- Browser acceptance: sign in as Alpha synthetic viewer, click **Open staging
  Control Center**, see 6 Alpha tickets and 3 customers. Confirm zero Beta
  tickets in Alpha view and no write buttons. Refresh/logout and verify
  session protection. Independently repeat with Beta synthetic test identity.
- No merge or production deployment without review; keep PR #33 and PR #35
  unchanged. No paid service.
