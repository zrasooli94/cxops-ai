# Phase 1Y — Synthetic agent qualification and release gate (2026-10-16)

**Classification:** Isolated staging only. **Ticket writes: HOLD / DISABLED.**
This document is evidence preparation, not an independent security signoff.

## Verified owner evidence

- The authorized agent completed the Neon Managed Better Auth OTP verification in the Preview browser.
- Owner's Neon `cxops_staging` Users table screenshot shows `emailVerified = TRUE` for the intended reachable agent mailbox.
- An existing, previously established Neon Auth UUID was mapped to exactly **one** `Synthetic Alpha Workshop` `agent` membership. The original Alpha and Beta viewers remain `viewer` in their separate organizations; the unused fictional agent remains without membership.
- Role review: `OrganizationRole.AGENT` has `ticket.read` and `ticket.write` within its resolved organization, but no tenant or admin delegation. Staging HTTP middleware permits only explicitly enumerated reads and an exact POST route behind a **separate disabled flag**.
- Post-authorization read-only DB evidence: 0 ticket assignments, 0 integration jobs, 0 AI request logs, 0 ticket events. No production data was copied.

## Independent infrastructure containment

- Neon: project `rough-hall-90121003`, branch `br-spring-truth-azv32ox5`, database `cxops_staging`; independent of production. Settings validates exact direct endpoint, username, database, AsyncPG and required TLS.
- Existing **Phase 1W read-only** Render API `srv-db8i2cjncjis73b49vig` remains untouched, Free and autodeploy OFF.
- New **Phase 1Y** Render Free API `srv-db8ufi3rjlhs73djeieg`, Singapore, autodeploy OFF, pinned deployment source `37d8216795c3b908d525e135c729077122e37522`. URL: `https://cxops-ai-phase1y-staging-api.onrender.com`. Render reported LIVE; startup confirmed exact staging schema and provider/worker disabled.
- Runtime `CXOPS_STAGING_TICKET_ACTIONS_ENABLED=false`; `CXOPS_STAGING_INITIALIZE_EMPTY_DB=false`; provider and notification secrets absent or empty. Feature-subject allowlist is pinned to the separate agent identity.
- Vercel: `cxops-ai` Phase 1Y branch only; Preview-only backend URLs use the new API. Separate cookie/setup secrets were freshly generated, not copied from the protected Phase 1W Preview. All 14 required variables are branch-scoped. Enrollment disabled; agent email-OTP sign-in can be enabled separately; **ticket action flag remains false**.
- Neon trusted origin: the actual Phase 1Y Vercel Preview branch alias; no arbitrary redirects or frontend-provided audience.
- Source `frontend/src/lib/staging/neon-policy.ts` pins the new Phase 1Y service and a regression refuses the older Phase 1W backend.
- Production Vercel aliases, Render production API, Nhost, master and draft PRs #33/#35/#36 stay unchanged.

## Security review observations

1. Exact verified Neon JWT issuer/audience, Ed25519 key, 15-minute expiry, signature, verified-email and ban checks remain mandatory. Backend tenant lookup derives permissions from SQL membership, never JWT role or a form-provided tenant.
2. UI and server actions verify the signed subject, Alpha organization name, durable `agent` role and `ticket.write`. All browser OTP entry is HTTPS; no code or password should be shared in chat.
3. OTP issuance is gated behind an exact branch-only email recipient and high-entropy temporary setup secret. Shared Neon Auth provides OTP expiry and throttling.
4. `/staging/ticket-action` remains hidden in production; staging middleware and handler both reject it unless explicitly enabled. A successful request can only mutate the one row-locked, fictional Alpha ticket after all origin/customer guards pass.
5. Staging auth-success logs redact real email; regression test present. No external Zendesk, AI, email/Telegram or integration work in the action path.
6. Recent full CI at source `37d8216`: 8/8 PASS, 3,093 backend passed / 2 skipped. Later source `8282748` adds the Phase 1Y API origin pin; its full CI run must finish green before final release.
7. **Independent reviewer requirement is not yet satisfied for the newly added agent sign-in and environment binding.** Do not represent this preparatory inspection as an independent PASS.

## Remaining controlled qualification

- Verify new Preview deploy from a commit containing all branch-scoped config, keep production alias mappings unchanged, and confirm agent sign-in with a fresh private Email OTP.
- Recheck signed-session expiry/replay and cross-tenant 403, live viewer 403 and Beta denial using only synthetic identities.
- Obtain a separate independent reviewer for authentication changes, subject allowlists, side-effect graph, ticket row locking and actual Render/Neon custody boundaries.
- If approved: explicitly enable only Phase 1Y Vercel **and** Render ticket-action flags for one synthetic operator, execute claim → release → claim → resolve with stale-state/concurrency/replay tests, re-audit zero integration and AI records.
- Re-disable any write capability if negative tests fail. Keep PR #38 draft and unmerged until a separately authorized merge decision.


## Free-tier cold-start incident and safe read recovery (2026-10-16)

- Owner signed in with the verified Phase 1Y Alpha AGENT identity, but the
  initial workspace showed staging API unavailable without membership data.
  This was not evidence of a missing database membership.
- Render Free logs showed the API shutting down at 09:18:34 UTC, restarting
  at 09:21:43 UTC and finishing startup at 09:22:35 UTC. Owner screenshot
  occurred around 09:21 UTC (5:21 PM Malaysia), during that cold start.
  The original frontend's 15-second read timeout was shorter than startup.
- The patch retains the bounded fetch timeout and adds a client-only
  read-only retry capped at three attempts in a four-minute browser-tab
  window. There is no keep-alive worker, no paid resource change, no client
  bearer token or tenant selector, and no write call.
- HTTP 401/403 fail closed as authorization denials and are never
  automatically retried. HTTP 502/503/504 and fetch timeouts can be retried;
  unrecognized or invalid response contracts remain unavailable.
- No staff permissions, production aliases, backend source, secrets or
  ticket-action flags are altered by this change. CI and a fresh Preview
  deployment must pass before considering the user-facing fix verified.
