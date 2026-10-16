# Phase 1W/1Y — Official Neon email verification handoff

**State:** Draft-only source until CI and branch-scoped Preview verification.

## Why
The previously created `phase1y-alpha-agent@example.test` cannot receive
verification mail. The owner supplied an accessible Gmail address for a
separate staging-only agent enrollment. That email is stored **only** in the
isolated Neon Auth user directory and in an encrypted, branch-only Vercel
environment variable; do not commit it to source control or expose it in
public UI labels. Existing fictional viewer identities are unchanged.

The new `/staging/agent-enroll` page does **not** create or sign in a
privileged user. It uses Neon Managed Better Auth's official Email OTP
verification API and only the existing verified Alpha **viewer** is allowed
to request a code. The real owner enters the code directly in staging. No
code/password is ever sent through ChatGPT or logged to application output.

## Safety boundaries
- `VERCEL_ENV=preview`, `neonStagingEnabled`, branch-only
  `CXOPS_STAGING_AGENT_ENROLL_ENABLED=true`, and the server-only encrypted
  `CXOPS_STAGING_AGENT_ENROLL_EMAIL` are all required.
- The target email is not read from the browser, URL or submitted form.
- The handler requires an already verified, allowlisted Alpha viewer.
  Beta, unverified users, guest sessions and agent itself are denied.
- This page never grants CXOps authorization or touches Neon membership
  records. Ticket mutations remain disabled in both frontend and backend.
- Sending a code requires the user's explicit click. Neon handles OTP
  generation, delivery, throttling, expiry and validation; the application
  never stores or returns the code.
- A successful SDK response is only a *verification request result*. Verify
  `neon_auth.user.emailVerified=true` independently before authorizing
  any staging agent membership.
- The provider's shared email service and rate limits still apply.
  Disable enrollment after the owner verifies the account to prevent reuse.

## Operator sequence
1. Validate frontend tests/build/typecheck and original isolation suite.
2. Review this new OTP route for recipient pinning and unauthorized use.
3. Set exactly two branch-scoped Vercel Preview vars on PR #36 ONLY:
   `CXOPS_STAGING_AGENT_ENROLL_EMAIL` encrypted with the owner-provided
   real reachable mailbox, and `CXOPS_STAGING_AGENT_ENROLL_ENABLED=true`.
   Never set either on Vercel Production or on Render.
4. Redeploy **Preview only**, confirm production aliases remain unchanged.
5. User opens the existing read-only staging, uses the new **Verify separate
   staging agent email** link and presses Send verification code. If Neon
   returns a failure, do not bypass auth or flip SQL `emailVerified`.
6. User enters OTP directly into the HTTPS staging form; no code sharing.
7. Independently inspect the matching Neon Auth user record to confirm
   verified status and no previously copied privileges.
8. Only after Phase 1Y security signoff, grant that exact verified subject
   one Alpha organization `agent` membership (never Beta or production)
   and set separately reviewed staging-only allowlist, subject and write
   feature flags. Do not enable broad ticket writes, notifications,
   Zendesk, AI or payments.
9. Preserve original Alpha and Beta viewer access evidence, confirm zero
   integration jobs and AI requests, and keep all PRs draft.

**Stop conditions:** Neon OTP email delivery unavailable on Free tier,
email verification API unsupported, unexpected recipient, insufficient
rate limiting, mismatched Neon subject, or any evidence of production
configuration reuse. Do not bypass or invent verification.
