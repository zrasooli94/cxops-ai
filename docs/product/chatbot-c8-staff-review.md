# CXOps AI — C8 staff claim-review preview

**21 October 2026 · DRAFT / SOURCE ONLY / PRODUCTION RELEASE HOLD**

This milestone brings the real offline **C7 Claim Review Studio** into the existing
authenticated control center, under **Evaluations → Open claim review**. It
does **not** add a production reviewer database, customer transcript access,
new API route, model call, paid evaluation or release-decision mutation.

## Customer-product outcome

An evaluation manager can open
`/evaluations/claim-review`, select a private C6 synthetic worksheet and
assess each statement alongside the exact versioned passage it cites.
Judgments use four labels (supported, contradicted, unsupported, unclear)
and the same evidence excerpt/rationale rules as C6/C7. Staff may download
a draft and re-import it in a subsequent session without losing the original
C6 case/fingerprint fields. The route does **not** write anything to the
CXOps backend or silently persist uploaded review text.

The page uses existing control-center navigation, a responsive review list,
customer-answer context, cited passage selection, claim-level editor, progress
indicator, private reviewer A/B exports and a reminder about independent
Python C6 adjudication.

## Authorization and tenant-safety boundaries

This is a **staff-only page**, not a public widget or a new business API.

- The existing **server** `(control-center)/layout.tsx` checks the
  authenticated cookie, authorized tenant membership and the active tenant
  before rendering the shell. The new claim-review server page additionally
  calls **`getMyAuthorizationReadOnly(activeOrganizationId)`**, using the
  same trusted organization selector and backend `/me/authorization`.
  It must receive **both `evaluation.read` and `evaluation.manage`** for
  that **exact** organization; a role name, imported CSV, query parameter,
  read-only evaluation access or another organization's privileges are never
  sufficient. Failures render the normal closed/insufficient-permission
  message without exposing internal authorization details.
- A nested `CapabilityRouteGuard` uses the shared
  `ROUTE_REQUIREMENTS["/evaluations/claim-review"]`, whose requirement is
  `evaluation.manage`; the parent evaluations layout still requires
  `evaluation.read`. This **client guard is UX**, while the server
  check is the actual route-protection boundary. Existing FastAPI backend
  remains authoritative for any other API request.
- The link is visible on the Evaluations page only when the signed-in
  organization's authorization view grants management. The page's state
  carries the currently selected trusted organization ID. A resumed/delayed
  file import scoped to Organization A cannot be displayed as Organization B.
- This page **does not provide independent human reviewer authentication**:
  reviewer A/B names inside the file remain self-attested. Tenant membership
  is verified for opening the preview, not as proof that two people judged
  claims independently. A real tenant-scoped review database with staff
  role assignment, separate write APIs and audit is deferred.
- C7's reviewed browser CSV parser/validator is copied byte-for-byte into
  `frontend/src/lib/claim-review/review-core.js`. A PR-only CI byte
  comparison **fails on any divergence** from
  `tools/chatbot-claim-review/review-core.js`. There is no weakened second
  client-side parser. The Python C6 `combine/score` remains authoritative
  for **source SHA-256 fingerprints**, reviewer completeness and adjudication.

## Local review workflow

1. A legitimate evaluation manager visits **Evaluations → Open claim review**.
   Everyone else should see the existing permission-denied UX on a direct
   navigation. This behavior is currently **source-only and unmerged**.
2. Generate the C6 fictional data locally from the repository root:

   ```bash
   python -m scripts.chatbot_claim_review prepare \
     --out c6-fake-review.csv
   ```

3. Each reviewer uses a **separate private copy** of the blank CSV.
   The page locks the reviewer role/identifier after importing. A reviewer
   must quote 8+ exact characters from a cited source for supported/
   contradicted or add a written rationale for unsupported/unclear.
4. Download a private draft or completed reviewer CSV. Browser history, tab
   closing and organization switching **do not persist** review contents.
   The output keeps the original source/fingerprint fields unchanged.
5. C6 Python privately combines the two independently reviewed files,
   requires adjudication of disputes and prints a non-certified scorecard:

   ```bash
   python -m scripts.chatbot_claim_review combine \
     --reviewer-one reviewer-A-completed.csv \
     --reviewer-two reviewer-B-completed.csv \
     --out combined-private.csv

   python -m scripts.chatbot_claim_review score \
     --sheet combined-private.csv --require-complete \
     --report c8-source-review-report.json
   ```

   **Never upload real customer messages or completed reviewer material to
   the public repository.** The current tool accepts synthetic dataset
   `c6-fictional-faq-20261021` only. A synthetic filename is not a PII
   detector; use only truly fictional material.

## Testing contract and non-goals

- PR-only frontend CI runs `npm test` (entire frontend suite), targeted
  authorization/source-contract tests, locked npm install, ESLint, Next.js
  route type generation, TypeScript checks and a full Next.js build.
- The exact Python C6 helper generates a fresh **unlabelled 12-claim CSV**.
  The in-app typed codec must import, edit and export it correctly under
  local Node contract tests. Both C7 codec files must be byte-for-byte
  identical; GitHub permissions are read-only and no production secrets,
  database endpoints, paid models, or real customer messages are used.
- The CI test demonstrates the **actual server page source code** uses a
  trusted read-only backend authorization function, and that its pure grant
  predicate rejects wrong-tenant/read-only/manage-only/fabricated
  capabilities. It does **not** exercise a live authentication server or
  prove browser rendering/accessibility on an iPhone.
- **No real browser pilot / actual independent human reviews / semantic model
  quality measurement** occurred in C8. No user may interpret staff page
  access as a production authorization to train or auto-publish customer data.
- Further product work: authenticated tenant-scoped reviewer assignments,
  signed source snapshots, durable reviewer comments/audit, a private
  adjudication queue, properly reviewed claim sampling from authorized
  model runs and actual iOS/desktop accessibility tests. Those require
  separate privacy/security review, database design and owner approval.

**Production RELEASE HOLD:** no merge, Vercel/Render deployment, paid AI
provider action, production DB migration, Phase 1Y ticket writes or
credentials movement. Keep the synthetic Render Cron suspended, real
GitHub worker unchanged, and the repository public until the owner
explicitly directs otherwise.
