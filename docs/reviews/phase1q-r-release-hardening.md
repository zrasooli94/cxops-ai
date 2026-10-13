# Phase 1Q-R — Commercial launch security, quality and acceptance

Scope: independent hardening of Phase 1Q (PR #19, branch
`feat/phase1q-commercial-saas-foundation`). Base reviewed: `23e6ec1`.
Production remains on `32dc64f` (Phase 1P.9R). **Nothing was deployed,
migrated, granted, enabled or created in any live system.** All runs used the
disposable PostgreSQL `cxops_phase1q_disposable_test`, synthetic identities,
a fake Nhost Auth endpoint and fake AI providers.

## 1. Findings and fixes

| # | Severity | Finding | Fix | Evidence |
|---|---|---|---|---|
| R1 | **High** | Invitation acceptance checked the recipient email only when the JWT carried an `email` claim. Nhost access tokens carry none by default, so in production any signed-in holder of a forwarded link could redeem it (owner invitations included). | Acceptance requires `VerifiedIdentity` from a configured source; default `disabled` fails closed (503). `nhost_userinfo`: Nhost Auth `GET /v1/user` with the caller's own token; `id == sub`, `emailVerified`, not anonymous/disabled. `jwt_claims`: signed `email` + `email_verified=true`. Verified email must equal the invited address. | `test_identity_verification.py` (14), `test_invitation_requires_verified_matching_identity`, `test_owner_invitation_from_provisioning_is_recipient_bound`, `test_invitation_acceptance_fails_closed_when_identity_not_configured`, E2E journey (token without email claim), browser run |
| R2 | **High** (pre-existing) | Self-service `POST /organizations` accepted a client-chosen `external_id`, which the operator manifest uses as the tenant slug: an attacker could pre-create an upcoming customer's slug and own the tenant the operator later configures. | `external_id` rejected on self-service (422); manifest planner refuses (plan ERROR + apply guard) to adopt a non-`active` organization. | `test_self_service_cannot_choose_the_manifest_slug`, `test_manifest_refuses_to_adopt_a_non_active_organization` |
| R3 | **Medium** | Staff-triggered AI (agent analysis, knowledge answer/search/ingestion, evaluation runs) was capability-gated but never budget-checked: unbounded spend by an active tenant's staff. | Route dependency refuses (429) once the tenant's estimated budget or the platform ceiling is used, from the same ledger. | `test_staff_ai_calls_refused_once_tenant_budget_is_spent` |
| R4 | **Medium** | Unlimited self-service organization creation (abuse / name squatting). | ≤ `SELF_SERVICE_ORGANIZATION_LIMIT` (2) unapproved organizations per identity (429). | `test_unapproved_self_service_organizations_are_capped` |
| R5 | **Medium** (CI) | Secret Scan failed on a synthetic JWT *issuer name* fixture (`d1f5b92`, generic-api-key). | Literal replaced by a named constant; `.gitleaksignore` suppresses exactly that historical fingerprint (and one triaged pre-existing master fingerprint, `ff7352a`). No path or rule exemptions. | Local gitleaks 8.30.1: PR range, working tree and **full history** clean; CI Secret Scan green |
| R6 | Low | Widget kept a working-looking composer (default theme) after the tenant was suspended/disabled and resumed stored sessions. | A refused config (403/404) hides the launcher and never resumes or polls. | `embed-connect.test.ts` (refusal suite), browser run step 15 |
| R7 | Low | Invite page told users with a wrong/unverified account to "ask for a new invitation" although the invitation was still valid. | Distinct retry state for 403/503. | browser run |
| R8 | Low | Setup lost draft domains on reload before the chatbot existed; phone layout hid the active step; theme radios had no visible keyboard focus; step buttons lacked status in accessible names. | Session-scoped draft; compact phone stepper; focus ring; aria labels. | browser run (375 px) |
| R9 | Low | Overview "last 24 h: 0 answered" contradicted monthly usage (it counts asynchronous turns only). | Relabelled, hidden when empty. | browser run |
| R11 | **Medium** (process) | With public lead capture off (required in production) no lead could exist, so no customer could be provisioned. | Operator-only `POST /platform-admin/leads` + console form; consent recorded as `operator-entered`. | `test_operator_can_record_a_lead_while_public_capture_is_off` |
| R10 | Info | Lead consent text was hard-coded in the client, so the stored `consent_version` could drift from what was shown. | API publishes the exact text + version; the form renders it. | `test_availability_publishes_the_consent_text_that_is_recorded` |

Verified *not* vulnerable (adversarial HTTP tests, `tests/test_phase1q_adversarial.py`):
tenant owner → operator (incl. forged role/operator/Hasura claims), unapproved
/ pending / suspended tenants on 13 paid or external routes, cross-tenant
onboarding/workspace/team by header and by path id, operator endpoints
called by tenants, allowance edits through any tenant contract, widget-key
and digest disclosure on four read surfaces, invitation token reuse/guess/
expiry/revocation/concurrency, lead enumeration and honeypot, duplicate
provisioning and concurrent widget creation.

Accepted residual risks: lead-form flood can exhaust the hourly ceiling (form
denial; CAPTCHA/edge rule needs a third-party decision); embeddings are
estimated, not metered per call; a 56 px transparent frame remains where an
unavailable widget would be (loader minimum, not changed to avoid touching the
cached customer-side script); `/sessions/state` and `/updates` stay readable
for a visitor's *own* session after suspension (history only; no AI, no new
messages) by the existing kill-switch contract.

## 1a. Verification results (HEAD of this hardening)

| Check | Result |
|---|---|
| Backend pytest, Python 3.12 (`-m "not integration"`, disposable Postgres) | 2299 passed |
| Backend pytest, Python 3.11 | 2299 passed |
| New 1Q-R backend tests | identity 14, adversarial 12, invitation/lead/privacy 9, E2E journey extended |
| Ruff (`app tests scripts alembic`), mypy (`app`) | clean |
| Frontend `npm test` / `test:auth` / `test:session` | 799 / 620 / 100 passed |
| ESLint, `next typegen` + `tsc --noEmit`, `next build` | clean / pass |
| Docker API + frontend images | built |
| Alembic base → head → 1p9b0001 → head, `alembic check` (fresh DB) | clean, head `1q0a0001`, no new migration in 1Q-R |
| Gitleaks 8.30.1: PR range, working tree, full history | no leaks |
| Widget first load vs master (same machine) | 578.6 / 177.1 KiB (raw/gzip) vs 576.0 / 176.8; no gsap/lenis |

## 2. Invitation identity verification — evidence

* Unit (fake Nhost Auth via `httpx.MockTransport`): success uses the caller's
  token; refused for unverified email, missing email, `id` ≠ `sub`, anonymous,
  disabled; 401 → 403; 5xx / non-JSON / network error → 503; `jwt_claims`
  refuses missing or unverified claims; default mode is `disabled`;
  `nhost_userinfo` without URL falls back to `disabled`; config rejects
  unknown modes and plain-http URLs.
* API: missing / unverified / forwarded identity → 403 and nothing redeemed;
  the real recipient then succeeds; replay → 404; expired, revoked, replaced
  → 404; 4 concurrent verified acceptances → exactly one membership; owner
  invitation from provisioning has the same binding.
* Browser (local stack, `INVITATION_IDENTITY_MODE=nhost_userinfo` against a
  local fake `/v1/user`, owner token **without** an email claim): forwarded
  account refused; owner refused while unverified; after verification "Try
  again" succeeds; member invited and accepted the same way; replay 404.

## 3. End-to-end certification (all 15 steps)

| Step | Result |
|---|---|
| 1–2 Prospect visits site, requests demo | Pass. Keyboard-only: skip link, Tab order (honeypot skipped), Space/Enter; confirmation focused and scrolled into view. Accessibility audit of 6 public pages: one h1, no heading jumps, every control labelled/named, no horizontal overflow at 375 px. |
| 3 Operator reviews/approves | Pass (console; lead follows its status). |
| 4 Provisioned in onboarding | Pass (#11811). |
| 5 One-time owner invitation | Pass (shown once). |
| 6 Verified Nhost identity accepts | Pass (forwarded + unverified refused first). |
| 7 Setup wizard | Pass at 375 px; reload mid-setup keeps domains. |
| 8 Chatbot disabled before approval | Pass: config and session 403. |
| 9 Operator approves | Pass; entitlements 300 turns / $3. |
| 10 Domain, chatbot, knowledge | Pass (fake embeddings). |
| 11 Installed on simulated site | Pass (`http://localhost:4173`, Ocean theme). |
| 12 Conversation in correct dashboard | Pass (1 conversation, 1 waiting handoff, install detected). |
| 13 Usage and estimated cost | Pass (1/300, $0.0001 of $3, labelled estimate). |
| 14 Restricted team member | Pass: agent sees Overview + operations only; usage/team "Not available for your role"; `/team`, settings, operator refused. |
| 15 Suspension stops public chat | Pass: open session and new sessions refused, no launcher, no polling; reinstate restores (config 200). |

The same journey is automated in `tests/test_phase1q_e2e_journey.py`.

## 4. Nhost onboarding readiness (Workstream F)

Facts: the frontend has **no sign-up flow**; Nhost access tokens carry `sub`
and Hasura claims but no email; Nhost Auth exposes `GET /v1/user` returning
`email`, `emailVerified`, `isAnonymous`, `disabled` for the token's user.

**Recommended for the first (operator-assisted) customer:**
1. Operator creates the customer's user in the Nhost dashboard for the
   invited address *or* sends Nhost's password-reset / magic-link to it, so
   the address is verified by the customer clicking a link only they receive.
   (Manual, per customer; owner approval D3.)
2. Set on Render: `INVITATION_IDENTITY_MODE=nhost_userinfo`,
   `NHOST_AUTH_URL=https://<subdomain>.auth.<region>.nhost.run/v1` (no secret).
3. Operator provisions the lead; sends the one-time link; the customer signs
   in and accepts. Mismatched or unverified accounts are refused.

**Self-service later:** enable Nhost email sign-up with email verification
required, add an in-app sign-up page, keep `nhost_userinfo` (or configure
signed custom claims and switch to `jwt_claims` to save one HTTP call per
acceptance). Both are provider configuration changes needing approval.

Not done (forbidden here): no Nhost setting changed, no account created, no
real email sent.

## 5. Budget and capacity (Workstream G)

Baseline (from the approved 1P.9 envelope): Vercel Pro $20, Render Starter
API $7, Neon Free $0, Nhost Free $0, OpenAI capped by
`PLATFORM_MONTHLY_AI_BUDGET_USD` (default $15) → **≈ $42/month**, inside $50.

| Tenants | AI conversations / month (assumed 300 each) | Est. OpenAI (at ~$0.001–0.002/turn) | Neon free plan (100 CU-h) | Fits $50? |
|---|---|---|---|---|
| 1 | 300 | $0.30–0.60 | Comfortable | Yes |
| 5 | 1,500 | $1.50–3 | Comfortable | Yes |
| 20 | 6,000 | $6–12 | Watch CU-hours (daytime traffic in several time zones keeps compute awake) | Yes, if ceiling ≤ $15–20 |
| 50 | 15,000 | $15–30 | Likely exceeds free compute → Neon Launch (~$19) | **No** at full allowance: platform ceiling would hand off part of the traffic to humans; either raise budget or lower per-tenant allowances |

Per-turn cost assumptions are estimates (`gpt-4o-mini` decision ± grounded
answer); the reservation ceiling is deliberately higher ($0.01/turn) so the
cap is never overshot. Phase 1Q additions are negligible: lead submissions
≤ 30/h by ceiling (3 statements each), operator console queries are
operator-initiated point/aggregate reads, onboarding/workspace reads are
indexed and user-initiated; no new background polling.

Unbounded-spend review: public-chat turns — bounded (reservations);
staff AI — **now bounded** (R3); unapproved tenants — no spend (mask + gates);
knowledge embeddings — bounded by R3 gate per request but costed by a fixed
estimate; lead capture — no paid call; invitations — one Nhost `/user` call per
acceptance (Nhost free tier). Provider-side hard limits (OpenAI project limit)
remain the final authority and must be set ≤ the platform ceiling (owner).

## 6. Production rollout order

Superseded: see [`docs/runbooks/phase1q-release-runbook.md`](../runbooks/phase1q-release-runbook.md)
(migrate from the release SHA before merging; Neon recovery branch; RISPU
checks; data-preserving rollback). The previous merge-first list here was
unsafe because Render and Vercel deploy `master` automatically.

## 7. First paying customer — recommendation

Onboard operator-assisted, with lead capture still off: the operator records
the request in the console (**Record a request from a call or email** - added
in 1Q-R because provisioning needs a lead and the public form stays off),
moves it to approved, provisions, sends the one-time link, and approves the
tenant after reviewing setup. Price and invoice outside the
product until a billing decision exists. Start with a conservative allowance
(e.g. 300 conversations / $3) and review usage weekly in the operator console.
