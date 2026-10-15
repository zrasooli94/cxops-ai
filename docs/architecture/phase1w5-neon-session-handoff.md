# Phase 1W5 — Official Neon session handoff, staging only

Status: draft implementation; authenticated staging is NOT live.

## Implementation

- Official `@neondatabase/auth` Next.js server SDK, pinned to `0.6.0-beta`.
- SDK middleware matches only `/staging` and refreshes session cookies in a
  mutable request context before rendering; `/staging/login`, production
  routes, other APIs and static assets are excluded.
- New dynamic `/staging` portal with server-action sign-in/sign-out, loading
  and generic failure states. Existing Nhost session, BFF and production
  authentication paths are unchanged.
- SDK `getSession()` must return a verified, explicitly enrolled subject before
  SDK `token()` can run. Enrollment uses at most ten exact UUIDs in the
  server-only `CXOPS_STAGING_ALLOWED_SUBJECTS` setting. No signup action exists.
- The identity token stays server-side. The portal calls only the exact isolated
  backend's read-only `/me/organizations` endpoint, follows no redirects,
  disables response caching, bounds the wait, and renders only validated
  organization IDs/names. It does not expose tokens or upstream errors.
- Production/local cannot activate this adapter. Both existing activation
  flags, provider `neon`, pinned backend and branch Auth URL, an enrolled
  subject list, and a separate 32+ character cookie secret are mandatory.
- The original Nhost preview gate stays closed. This does not yet replace all
  Control Center clients with a dual-provider session abstraction. The new
  portal is the initial read-only proof surface.
- Corrected the older production-validator test to build a valid isolated
  staging configuration; no runtime validation was weakened.
- Explicit AJV 8 development peer satisfies the SDK's transitive UI resolver
  under CI's npm 10; ESLint retains its own compatible AJV 6 dependency.

## Verified infrastructure inspection

As of 2026-10-16 (Asia/Kuala_Lumpur), the connected Neon API reported:

- Project `rough-hall-90121003`, branch `br-spring-truth-azv32ox5`.
- Managed Better Auth already provisioned for `cxops_staging`.
- Zero public application tables and zero auth users. No customer data copied.
- Trusted origins empty; signup currently enabled by the provider.
- Vercel branch preview is READY and serves the fail-closed login notice.
- Render connector returned `no workspace selected`. Its tool instructions
  require explicit user selection before a workspace-scoped service call.
  Discovery returned `Zrasooli94` (`tea-d9ugi8vavr4c73af76ug`). No prior explicit
  selection was recovered, so no Render service was inspected or modified.

## Activation work still required

1. Confirm the Render workspace through the supported connector flow.
2. Inspect the existing Free bootstrap service and its budget/auto-deploy settings.
3. Review and apply the existing Alembic migration chain only to the empty
   isolated database; create synthetic tenants and memberships, and test A/B
   isolation. Do not stamp an uninitialized schema or copy production data.
4. Enroll synthetic verified Neon users, set the exact subject allowlist,
   configure the exact preview trusted origin and unique cookie secret.
5. Run the provider-disabled real API on the existing Free staging service;
   verify schema head, JWT rejection, membership enforcement and zero provider
   or notification activity.
6. Set branch-specific Preview flags only after independent isolation review.
7. Verify real sign-in, rejected credentials, session restoration, expiry,
   logout, unauthorized subject and cross-tenant access in a browser.

The portal's disabled state and offline policy tests do not certify these
live flows. Keep PR #36 draft; no security-sensitive merge, production
configuration, production database change, paid resource or paid action.
