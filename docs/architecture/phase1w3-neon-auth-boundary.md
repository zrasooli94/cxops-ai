# Phase 1W3 — Staging-only Neon Auth implementation boundary

**Engineering state: IN DRAFT. Not a working sign-in or production release.**

## Verified separate identity and resources

- Production: Nhost + RSA/RS256 JWKS; unchanged.
- Staging: Free Neon project `cxops-ai-staging-synthetic`, independent
  PostgreSQL `cxops_staging`, Neon Managed Better Auth (`better_auth`).
- Staging identity issuer/audience are **the HTTPS origin** of the Neon Auth
  service. Access tokens are Ed25519/EdDSA (not production RS256) and last
  around 15 minutes.
- A separate Free Render bootstrap service remains unauthenticated,
  read-only and disconnected from the staging database.
- Staging Vercel branch has its own bootstrap backend URL; both Preview
  authentication activation flags remain **false**.

## What the current changes implement

- `staging/neon_jwt.py` authenticates short-lived, email-verified,
  non-banned synthetic identity tokens with a pinned staging Auth origin.
  It rejects incorrect algorithms, issuer/audience, expired claims,
  mismatched subjects, malformed JWKs and unauthorized key types.
- Keys come from the staging project's *exact* HTTPS JWKS URL. Requests
  follow no redirects, are size/time bounded and fail closed.
- `app/core/auth.py` selects that verifier only with
  `ENVIRONMENT=staging` and `AUTH_MODE=neon_staging`. Production Nhost JWKS
  selection is otherwise unchanged.
- `app/core/config.py` pins the staging database hostname, database,
  Postgres role and TLS policy to the brand-new staging project. It also
  refuses debug/dev bypasses and configured live OpenAI, Zendesk, generic
  machine webhook or email/Telegram notification credentials.
- The runtime Dockerfile includes the staging verifier module, but the
  existing production backend has not been rebuilt or deployed.
- Adversarial synthetic-only tests cover verification and configuration;
  the full GitHub CI result must be inspected before certification.

## Security conditions still required BEFORE real staging login

1. Integrate **Neon's official Next.js Managed Better Auth SDK** with the
   Control Center as a staging-only provider; keep Nhost as the untouched
   production provider. Do not improvise cookie forwarding or accept an
   arbitrary bearer token from client JavaScript as an authenticated session.
2. Configure a separate staging-only session cookie secret, trusted preview
   origin, logout, refresh and token-to-API handoff. Never reuse production
   sessions, keys or passwords.
3. Review Neon Auth enrollment settings. It currently has email/password
   sign-up enabled by default and no synthetic test accounts. Restrict access
   to explicitly enrolled synthetic test users before publishing sign-in.
4. Run the existing Alembic migrations ONLY on the empty
   `cxops_staging` database, set up synthetic organization memberships and
   data, and test cross-tenant isolation. Neon-managed `neon_auth` tables
   alone do not constitute the CXOps application schema.
5. Provide a provider-disabled, worker-safe staging API that cannot
   issue LLM calls, reach Zendesk, dispatch notifications, or perform paid
   business actions. Existing Render bootstrap is **not** that API.
6. Independently verify the full frontend, backend and identity topology,
   then enable branch-specific Preview access flags as the LAST step.
7. Recheck total free-tier limits and existing account usage before any
   staging build or service increases. No paid plan upgrades authorized.

## Stop conditions

An attempted login still uses Nhost, a token is signed for another Neon
project, a staging backend URL points to production, a database connection
does not match the pinned staging hostname, or any real provider secret is
present. Fail closed and do not deploy.

**Current result:** the staging backend can verify a signed identity
*in isolation*; users cannot yet log into a real CXOps staging workspace.
