# Phase 1W6 — Authenticated read-only staging

Verified 2026-10-16 (Asia/Kuala_Lumpur). Draft only; no production release.

## Working environment

- Frontend: https://cxops-ai-git-feature-phase1w-preview-isolation-gate-zrasooli95.vercel.app/staging
- API: https://cxops-ai-staging-api.onrender.com
- Render workspace `Zrasooli94` (`tea-d9ugi8vavr4c73af76ug`), explicitly confirmed by the owner.
- API service `srv-db8i2cjncjis73b49vig`: Free, Singapore, one instance, automatic deployment OFF.
- The previous bootstrap service remains a holding server. The connector cannot modify existing build/start commands; a separate Free API service was provisioned.
- Database: existing independent Neon project `rough-hall-90121003`, branch `br-spring-truth-azv32ox5`, database `cxops_staging`. No production branch cloning.
- The existing Alembic chain initialized an independently verified empty public schema: 36 application tables at revision `1q0a0001`.
- `staging.runtime` refuses production, nonempty unversioned/behind/ahead schemas and ambiguous heads. Empty initialization requires explicit opt-in and uses a staging advisory lock. The opt-in was removed after initialization; subsequent startup verified the exact head.

## Identity and isolation

- Official Neon Next.js SDK with scoped `/staging` middleware, server actions and server-side token handoff.
- Two synthetic identities, each with a distinct viewer membership in Alpha or Beta Workshop. Synthetic email verification was marked by the operator in this isolated test directory; email delivery was not tested.
- Exact subject allowlist, exact backend/Auth URLs, unique staging cookie secret and trusted preview origins.
- All Vercel settings are scoped to Preview and `feature/phase1w-preview-isolation-gate`. Production credentials and settings are not reused.
- The synthetic-access button signs into the fixed Alpha viewer via the official SDK. Its generated password remains server-side in a sensitive branch variable; no password or token is sent to client components.
- Main Control Center/Nhost routes remain gated. This is the initial read-only staging qualification surface, not a full writable customer workflow or a production authentication migration.

## Verification evidence

- Inherited provider-secret fixture corrected by explicitly pinning provider/notification values; regression injects synthetic process values and proves fixture isolation. Production and staging runtime validators were not weakened.
- Fixture-fix CI at `053da3c`: all jobs passed; backend **3031 passed, 2 skipped**.
- Local configuration/JWT/runtime/initialization regression set: **153 passed**.
- Frontend auth tests **625 passed**; staging tests **19 passed**; lint, TypeScript, Preview build and zero-forwarding HTTP smoke passed.
- Live API: both synthetic identities obtain Neon tokens and receive only their own organization; own tenant 200, opposite tenant 403 in both directions.
- Missing/invalid tokens 401. Organization writes, webhook, AI action and OpenAPI routes 503. Readiness 200.
- Invalid provider credentials return 401 and create no session.
- Real browser: synthetic sign-in, verified API identity, own organization rendered, reload restoration, sign-out, denial after sign-out and re-sign-in all passed.
- Browser automation used the supported cloud browser because the local Chromium download was unusable. Browser-extension metadata errors were unrelated to application behavior.
- Runtime logs confirm no staging workers/provider warmup. Database audit after tests showed no integration jobs or AI requests.
- JWT expiry/claim/signature rejection has offline adversarial coverage. Live long-lived-token expiry and session-cache refresh evidence, if completed, is recorded in the final PR verification summary; do not infer it from basic sign-in.

## Production containment and release limits

No production database, credentials, customer records, source branch or security-sensitive PR was changed or merged. No paid plan was activated.

Vercel's manual Preview deployment automatically assigned the generic project alias `cxops-ai-zrasooli95.vercel.app` to the preview. This was detected and restored to the existing production deployment `dpl_CPghfGgZ36UiDhTNoFCV95Nb2LWk`. The primary `cxops-ai.vercel.app` and master alias stayed on that deployment. Future manual Preview deployments require checking generic aliases afterward.

Keep PR #36 and production/security-sensitive PRs unmerged pending review. Staging writes, public chat, webhooks, AI/provider calls and notifications remain disabled. Free compute may sleep; cold starts are expected. The next product milestone can migrate the complete Control Center to the staged identity abstraction and expand reviewed synthetic read routes.
