# Phase 1W — Isolated authenticated staging, release gate

**State:** Preparation only. NO staging service, test identity tenant, or empty
database has been provisioned or funded. Nothing here authorizes production
changes, a live deployment, copying production data, or emailing customers.

## Verified baseline

- Vercel project `cxops-ai`: `NEXT_PUBLIC_NHOST_SUBDOMAIN`,
  `NEXT_PUBLIC_NHOST_REGION` and `NHOST_SESSION_COOKIE` are
  **production-scoped**. `BACKEND_API_URL` currently applies to both
  Production and Preview, with no verified separate staging target.
- Render has a production CXOps Docker service attached to `master` with
  automatic deployment enabled. There is no separately verified CXOps staging
  service in that workspace.
- Neon `cxops-ai`: current project contains a primary production branch and
  prior branches initialized with **parent production data**. Do not treat
  existing branches as empty testing datasets; never expose copied customer
  records in an experimental preview.
- PR #33 (Zendesk replay protections) is deliberately draft with a pending
  production migration decision. PR #35 (Phase 1V handoff dashboard and
  synthetic interaction demo) is independent and draft.

## New defensive behavior on this branch

The Vercel Preview environment fails closed for:
1. The Control Center sign-in form and the server sign-in action.
2. `/api/backend/**` authenticated staff proxy routes.
3. Direct server tenant/authorization backend clients.
4. `/api/public/chat/**` public conversation proxies.
5. `/api/public/leads` early-access lead proxy (read and write).

No user-provided cookie, JWT, chat token, widget key, request body, or
credential is forwarded to the shared production backend when blocked.
The preview sign-in page explains the configuration and links to the entirely
synthetic, separately deployed Phase 1V interactive handoff demo.
Production and ordinary local development behavior remain unchanged.

This is a **defense-in-depth application gate**. The two server-side enabling
flags and matching URLs cannot cryptographically prove that staging has no
production database connections. That requires external infrastructure audit.

## Preflight before allowing actual staging authentication

1. **Budget and resources:** Owner reviews monthly charges and confirms the
   total operating budget target (below $50/month). Do NOT create a paid Render
   service, Neon project/branch or new Nhost tenant as a side effect of this PR.
2. **Database:** Provision a fresh EMPTY staging database with new database
   credentials; run only approved migrations and insert synthetic seed data.
   No production Neon branch cloning, copied dumps, real customer information,
   production credentials, or direct production read access.
3. **Backend:** Isolated Render staging service built from an explicitly chosen
   commit; use staging database URL only, isolated public host, and separate
   nonproduction credentials. Disable external Zendesk calls, webhook
   writebacks, live AI, email/Telegram notifications and paid actions.
   Confirm both startup schema gate and worker safety. Prevent automatic
   production deployment triggered by any development branch changes.
4. **Identity:** Distinct Nhost development tenant with synthetic users and
   verified test JWT issuer/audience/keys. Do not reuse production user
   credentials or blindly trust matching `NEXT_PUBLIC_*` strings.
5. **Vercel Preview variables:** Apply **branch-scoped** Preview values only
   after external verification. Keep Production variables unchanged:
   - `BACKEND_API_URL=https://<verified-staging-backend>`
   - `CXOPS_STAGING_BACKEND_URL` = exactly same HTTPS origin
   - `NEXT_PUBLIC_NHOST_SUBDOMAIN`, `NEXT_PUBLIC_NHOST_REGION`
     = identity settings of the independent staging Nhost tenant
   - `CXOPS_STAGING_NHOST_SUBDOMAIN`,
     `CXOPS_STAGING_NHOST_REGION` = corresponding staging identity settings
   - `CXOPS_STAGING_ISOLATION_VERIFIED=true` only after data isolation audit
   - `CXOPS_PREVIEW_AUTH_ENABLED=true` only after auth and API smoke tests
6. **Smoke tests:** Test valid/invalid login, clean test membership, tenant A/B
   isolation, assign/release/resolve, expired JWT, unauthorized staff access,
   errors and redirects, and real UI browser navigation *using synthetic
   resources only*. Confirm an unconfigured preview returns 503 on all
   forwarding APIs without opening network connections.
7. **Release:** Request independent review. Do not change the production
   account, DB, Render service or Vercel production aliases for staging.
   Keep PR #33 and its unapproved migration separate.

## Stop conditions

- No safe empty test database, separate Nhost tenant or dedicated backend.
- Preview backend origin still points at `cxops-ai.onrender.com`.
- Reused production credentials, copied customer records or unexplained spend.
- Missing or invalid isolated JWT issuer/audience/tenant ownership.
- Any test sends a real webhook, model request or customer notification.
- Backend cannot be demonstrated to have zero production database access.

**Current result:** groundwork only; flags are not enabled, and no live
authenticated staging session is claimed.
