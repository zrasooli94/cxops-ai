# CXOps AI — C12 staff review API preview (source-only)

**21 October 2026. DRAFT. RELEASE HOLD. No production/staging deployment.**

C11 introduced a default-OFF, development-only FastAPI API to the fictional
C6 source-fingerprinted, C10 tenant-owned review schema. C8 already provides
a protected staff page using private local CSVs. C12 adds an **optional
synthetic API panel** alongside that existing offline workflow.

## Explicit two-sided activation gates

- Next must be running in **development** with
  `CXOPS_C12_SYNTHETIC_REVIEW_PREVIEW=true`. The staff server route
  hard-blocks the panel when `NODE_ENV=production` even if the variable
  is mistakenly set. It checks the current organization's
  `evaluation.read` and `evaluation.manage` grants before rendering.
- FastAPI separately requires `ENVIRONMENT=development` AND
  `CLAIM_REVIEW_SYNTHETIC_API_ENABLED=true`, plus the authenticated staff
  JWT, trusted tenant membership, and C11 capability checks. Default is
  OFF and staging/production are not eligible for C11 at all.
- C10 tables would be required **only in a separately approved local
  development test database**. This PR does not migrate any database,
  deploy code, modify secrets, enable either flag, or authorize real
  customer data. All offline CI runs with preview flags absent/OFF.

## What the UI actually supports

The existing private CSV review flow is unchanged and still appears by
default. In a deliberately enabled local development preview, a new panel
allows:

1. Evaluation administrators with `member.manage` to create a
   **fictional C6** claim batch and assign exactly two distinct
   eligible reviewer subject IDs.
2. An assigned reviewer to load **their own** claim queue, seeing only
   claim keys, frozen fingerprints and their own completion flags.
3. A reviewer to load a local fictional C6 worksheet in memory; the
   browser requires an **exact fingerprint match** between the private
   queue entry and that local row before enabling submission. Source
   passages are never uploaded or requested from the backend.
4. Submission of a label and bounded cited excerpt/rationale over the
   same-origin **existing authenticated Next BFF**. The browser does not
   send bearer tokens, tenant IDs, actor IDs or arbitrary headers. FastAPI
   retains final membership/RBAC, source, separation and audit decisions.

The panel uses generic error messages and refreshes auth after 401/403.
The organization-scoped React parent is keyed to the server-selected
tenant, with a stale-request guard to prevent applying asynchronous
responses from the previous organization. Nothing is persisted in browser
storage. Offline CSVs remain the authorized default until separate
rollout approval.

**Adjudication is API-only in C12.** A third-party reviewer should not
be able to see either reviewer's hidden vote. A secure, dispute-only
adjudication queue has not yet been designed; merely adding a checkbox
without a trustworthy eligible-claim list would be misleading.

## Privacy and security requirements

- No production content, customer transcript, real source documents, raw
  prompts, external AI calls, billing operations or business tools.
- C11 API is synthetic-only. An arbitrary JSON dataset or reviewer-supplied
  organization selector is not accepted. The C10 hash and immutable
  event storage remain the backend authority.
- A synthetic fixture slug is not a PII detector. **Do not place actual
  customer content into the local file or change fixture names to simulate
  real data.**
- The browser's `C7` parser verifies schema/fingerprint *shape*, while
  C10 verifies canonical source snapshots server-side. Reviewer subject
  authorization is supplied only by the verified JWT, not by fields in CSV.
- A frontend `evaluation.manage` check is UX defense-in-depth and cannot
  replace FastAPI's current DB role and tenant membership checks.
- No reviewer independence cryptographic proof, PostgreSQL RLS principal,
  encrypted real data, retention/deletion procedure or third-party
  signed adjudication exists yet.
- No true deployed preview or real-account UAT is claimed by offline
  frontend tests. Source-only UI code does not prove end-to-end login or
  actual Nhost/JWKS authentication.

## Offline acceptance

The C12 PR-only workflow enforces read-only GitHub checkout and empty
provider/DB secrets, uses locked npm dependencies, runs the complete
frontend Node suite, validates the same-origin API adapter and explicit
server flag, rechecks the unchanged C7 parser, ESLint on modified
reviewer components, Next route types, strict TypeScript and Next build.
It never enables C11 or starts a backend service, so it provides
**unit/build certification only**, not end-to-end C12 API interoperability.

Next secure milestone: an isolated combined Next+FastAPI disposable
Postgres browser test with genuinely synthetic local JWT fixtures and
an approved development-only preview, followed by private dispute-only
adjudication review and explicit staging rollout approvals.

**RELEASE HOLD: no merge, production or staging deploy, C10 migrations on
real environments, paid provider, new secret, Render Cron activation,
Phase 1Y writes, or repository visibility change.**
