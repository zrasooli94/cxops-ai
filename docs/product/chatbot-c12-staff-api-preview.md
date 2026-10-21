# CXOps AI — C12 browser-to-backend synthetic staff reviewer preview

**21 October 2026 | Source-only draft | Production RELEASE HOLD**

## What changed

C11 offers disabled-by-default authenticated FastAPI reviewer APIs, but the
staff Evaluations → Claim Review screen from C8 still operates exclusively
with private CSV files. C12 adds a **separate, optional development-only
panel** to the existing reviewer. It preserves the working local CSV flow,
but lets authorized staff exercise the source-hashed C10/C11 audit in a
controlled, fictional tenant. The panel supports:

- Create an immutable batch of the **committed fictional C6 12-claim set**.
- Assign exactly two different, currently eligible reviewer subject IDs.
- Fetch **only the signed-in person's** assigned review keys, exact source
  fingerprints and own `completed` flags (never another reviewer's
  identity/label/evidence).
- Compare a backend fingerprint against the active, **locally imported C6**
  claim before permitting a new review submission. The backend itself
  verifies the canonical C6 source SHA-256 and current reviewer membership.
- Submit a source-quoted or explained, closed-label judgment **once** per
  assigned reviewer, or adjudicate as a distinct third staff member only
  after the two original reviewers actually disagree.
- Reopen your private queue to see the backend-recorded completion state.
  It does not automatically import others' judgments or turn labels into a
  chatbot release approval.

The source-only UI is implemented at
`frontend/src/app/(control-center)/evaluations/claim-review/`, using a
typed, closed-path client in `frontend/src/lib/claim-review/private-api.ts`.

## Dual, OFF-by-default release barrier

The C12 **Next server** renders the panel only if BOTH are true:

- `ENVIRONMENT=development` (not staging/production)
- `CXOPS_C12_SYNTHETIC_STAFF_PREVIEW_ENABLED=true` (default unset)

The C11 **FastAPI backend independently** refuses the entire endpoint
family unless BOTH are true:

- `ENVIRONMENT=development`
- `CLAIM_REVIEW_SYNTHETIC_API_ENABLED=true` (defaults false)

Both flags are enabled together **only** in the disposable GitHub CI
environment. No real service, staging instance or production environment
has been authorized to enable them. A Next production-mode build in CI
does not change the runtime `ENVIRONMENT=development` gate.

## Authenticated browser-to-API path

`/evaluations/claim-review` is already a server-authenticated
organization-selected page; C8 requires `evaluation.read` and
`evaluation.manage` on that exact tenant. The optional C12 panel inherits
that guard and cannot be enabled by a browser parameter or input CSV.

`private-api.ts` sends only closed typed requests to the EXISTING
same-origin `/api/backend/evaluations/claim-reviews` BFF. It NEVER
chooses an `Authorization` bearer token, JWT subject or
`X-CXOps-Organization-ID`. The BFF derives these from the existing
authenticated staff session and trusted active organization cookie, then
forwards them to C11. FastAPI validates the actual JWT, resolves the
membership-selected tenant and enforces capability + per-reviewer assignment
for **every** request. C10 service additionally rechecks current DB
membership/role/lifecycle and binds every audit row to that tenant.

The UI is **not an authorization boundary**. The reviewer action button
checks that the own-queue claim/fingerprint matches the locally loaded
C6 worksheet. A third-person adjudicator can act from the fictional
source even without an assigned review queue, but FastAPI refuses
self-adjudication or undiscussed claims. The frontend hides admin batch
setup when `member.manage` is missing; the backend is authoritative.

All error messages are bounded, generic, and avoid echoing raw backend
SQL/reviewer/source details. A 401/403 triggers staff authorization
refresh. Async responses are scoped to the previously selected tenant,
and the control-center provider is keyed by the organization, remounting
state on a tenant switch. No LocalStorage, customer data upload, durable
client review cache, paid LLM call or email/tool execution.

## Full source-only functional acceptance

The C12 PR GitHub Actions workflow is the first end-to-end test to combine
**genuine Chrome + actual Next production-mode BFF + actual FastAPI HTTP
JWT/RBAC + actual disposable PostgreSQL 16** in the *same* isolated run.

- A hard preflight invokes the existing exact C2 GitHub Actions disposable
  guard, requiring `127.0.0.1:5432/cxops_chatbot_c2_disposable_test`, no
  provider credentials, development flags and no public-chat writes.
- The C10 Alembic head is applied to that disposable database only, and
  `scripts/c12_seed_disposable_staff.py` creates **fictional** owner,
  reviewer A, reviewer B, third adjudicator, viewer and second tenant.
- The app uses **only** an explicitly fictional local HS256 test secret
  `c` repeated 32 times. Browser staff sessions are local signed JWTs
  with a synthetic issuer/audience. No Nhost account, real issuer/JWKS,
  customer conversation, real email or provider calls are touched.
- Browser requests are restricted to `127.0.0.1` on the two dedicated
  test ports. FastAPI starts with `--lifespan off` to avoid launching
  maintenance workers or provider warmup, not to weaken API auth; the
  actual FastAPI dependencies, routes, SQL connections and audit triggers
  are exercised.
- Real Chrome visits the authorized staff page and creates a batch/
  two reviewer assignments; reviewer A imports the fictional CSV and
  submits a directly quoted contradicted claim, reviewer B sees their
  own separate uncompleted queue and submits a differing unsupported
  judgment, and a third distinct staff user adjudicates. Unauthorized
  viewer, unauthenticated client and Org B cross-tenant requests fail.
- The same real browser switches between fictional organizations and must
  lose all Org A local CSV/queue state. The client supports only a bounded
  source-matching fictional claim; no user-controlled actor/tenant IDs.
- An **independent PostgreSQL audit script** asserts exactly one fictional
  batch, 12 C6 claim items, two immutable reviewer assignments, two
  reviews and one adjudication, no Org B audit rows and only hashes, not
  raw quoted source content. No model is invoked.
- Full frontend Node tests, targeted ESLint, Next route type generation,
  strict TypeScript, production-mode build and prior C10/C11 API/FK/trigger
  regressions all run on the final exact PR head.

The successful workflow, if green, supports **real local synthetic browser
integration**, not a production or staging service rollout. Red CI must be
reported and fixed before describing this milestone as tested.

## Gaps requiring separate approval

1. **No real Nhost/JWKS** or staff identity-provider UAT. Fake HS256
   tests prove the actual dependency chain on fictional credentials,
   not the production issuer and refresh behavior.
2. **No real customer data**, no extraction from live conversations,
   no independent human reviewer signing/identity verification, and no
   evidence that a language model is factually correct. C5 citation
   contradictions are limited; C6's human assessment and C12's fake
   examples cannot certify semantic entailment.
3. **No running database migration** outside disposable CI. C10's
   audit triggers are not cryptographic WORM; there is still no
   least-privilege tenant RLS principal in Postgres.
4. **No actual Safari/Android/VoiceOver/TalkBack certification**.
   Headless Linux Chrome covers functional browser behavior only.
5. **No persistent/staff-facing case source text in the database**.
   Reviewers must import their own C6 synthetic worksheet to view exact
   passages. Do not upload private customer text into a fake C6 form.
6. **No production approval** from any reviewer verdict. Auto-reply
   publication, release decisions and business tool execution are
   independent and unchanged.

**HOLD:** Keep all chatbot/worker PRs draft, Render synthetic Cron suspended,
Phase 1Y real ticket writes off, all provider keys private, production
unchanged, and the GitHub repository's current visibility unchanged until
the owner explicitly authorizes a separate rollout/security review.
