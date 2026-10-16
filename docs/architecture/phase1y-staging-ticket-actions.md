# Phase 1Y — Controlled synthetic ticket workflow (draft)

**Scope:** This work is stacked on Phase 1W/1X draft PR #36, which
established the independent Free-tier Neon database, Neon Auth identity,
Render staging API and read-only Control Center. Production branch, Nhost,
Zendesk, email/Telegram, AI provider and all customer data stay unchanged.

## User-visible milestone

Once independently reviewed, the enrolled **Synthetic Alpha Agent** may
open the staging Control Center, claim one fictional ticket, release it,
claim it again and resolve it. The database and ticket metrics should update
after every action. Synthetic Alpha Viewer must remain read-only. The Beta
viewer and Beta tickets must stay outside Alpha's membership.

This is **not** a production rollout or a claim that every production ticket
side effect is being simulated. It is the first isolated, controlled write
validation using actual staging PostgreSQL and tenant/RBAC dependencies.

## Backend containment

- Hidden `POST /staging/ticket-action` only, no general ticket mutation
  endpoints exposed to staging.
- Off-by-default two-layer gate: staging HTTP middleware and route each
  refuse unless `CXOPS_STAGING_TICKET_ACTIONS_ENABLED=true` is present
  on the **separate Free staging API**.
- `ENVIRONMENT=staging`, `AUTH_MODE=neon_staging`, an enrolled Neon
  identity whose exact subject equals the server-side
  `CXOPS_STAGING_TICKET_AGENT_SUBJECT`, real Alpha AGENT membership and
  `ticket.write` capability must all be true.
- Tenant resolution comes from the durable organization membership, not
  a form-supplied tenant id. Exact organization name must be
  `Synthetic Alpha Workshop`.
- Ticket row locked `SELECT ... FOR UPDATE` with exact tenant predicate.
  Only `source=synthetic_staging` and `PHASE1X-ALPHA-*` records whose
  subjects begin `Synthetic: ` are mutable.
- Request model allows only `ticket_id` and `action` (claim, release,
  resolve). Foreign/unsafe records return generic 404; incompatible states
  return 409. Concurrent claims serialize at the row.
- The action changes only `assigned_subject`, `status` and
  `resolved_at` on that fictional ticket, then commits.
- No payment, email/Telegram, Zendesk webhook, general ticket service,
  conversation synchronization, AI/RAG, notification worker or
  integration job is used. Existing staging background workers remain OFF.
- Existing fixed synthetic staging ticket IDs allow repeatable, bounded
  tests. Reopening a resolved ticket and production SLA synchronization
  are deliberately out of scope, and need separate review.

## Frontend containment

The official Neon SDK session and access token remain server-only.
The app requires the existing Preview identity gate plus the same branch-only
action feature flag and exact subject. It independently requests
`/me/organizations` and `/me/authorization` from the staging API and
renders claim/release/resolve controls only for an Alpha test AGENT holding
`ticket.write`. Server actions revalidate the subject, verified tenant,
capability, and backend state before posting to the pinned staging origin.
All caller-supplied ticket ids are treated as untrusted; the backend owns the
final authorization. Viewer UIs remain read-only.

## Required checks before any write enablement

1. CI lint/mypy, frontend build/typecheck, original read-only staging tests,
   Docker, Schemathesis, secret scan and the **full backend suite** green.
2. Disposable PostgreSQL/ASGI security tests: viewer 403; cross-tenant or
   non-synthetic IDs 404; forged body 422; replay 409; concurrent claim
   single winner; correct release/resolve, no integration/AI records.
3. Independent review of signing, tenant identity, role permissions,
   off-by-default flags, ticket locking and any general ticket side effect.
4. Provision a *separate* synthetic Alpha test AGENT in the isolated Neon
   directory; do **not** promote existing Alpha Viewer (preserve negative
   access evidence). No real identities or external delivery.
5. Verify no staging provider keys or paid services; staging API remains
   Free, autodeploy OFF, production Nhost and DB untouched.
6. Explicitly enable the two isolated staging-only flags **after** review,
   never on production or broad Vercel Preview. Deploy only staging services.
7. Browser acceptance: agent claim/release/resolve updates DB and UI;
   concurrent requests, stale-state and Beta isolation verified; viewer
   still sees no write controls; audit zero integration jobs/AI requests.
8. Keep PR #38 DRAFT and don't merge to PR #36/master until signoff.

**Current status:** code in a draft PR, flag off, no new agent account or
deployed writes. This is not yet a feature the owner can use in staging.


## Post-verification, private staging-agent sign-in (default OFF)

Neon email verification alone is not a session. A separately opt-in
\`/staging/agent-signin\` route uses the **official Neon Managed Better Auth
Email OTP sign-in** method without requiring a password in ChatGPT.

- The recipient remains the branch-only server-fixed enrollment mailbox.
  The browser cannot supply the recipient, token, or backend URL.
- Requires Preview-only isolation, \`CXOPS_STAGING_AGENT_SIGNIN_ENABLED=true\`,
  a pinned UUID \`CXOPS_STAGING_TICKET_AGENT_SUBJECT\` included in the isolated
  staging subject allowlist, and a private high-entropy setup code to request
  the email OTP. Neon owns OTP generation, expiration, attempt limits and email
  delivery; the app does not persist, log, or return the OTP.
- The owner enters the OTP only in the Preview interface. A signed-in Neon
  session is **not proof of Alpha AGENT authorization**. The UI and backend
  independently enforce \`emailVerified\`, pinned subject, durable membership,
  exact tenant and \`ticket.write\`; user self-signup cannot create privileges.
- Switch off \`CXOPS_STAGING_AGENT_ENROLL_ENABLED\` after confirmation, and
  separately switch on the sign-in flag **only after** independent verification
  plus membership review. Keep \`CXOPS_STAGING_TICKET_ACTIONS_ENABLED=false\`
  on both frontend and backend pending write-release acceptance.
- To deploy Phase 1Y without touching production or the PR #36 API, pin an
  isolated, reviewed staging backend to the exact Phase 1Y SHA rather than
  redeploying the existing Render service that tracks Phase 1W. Review any
  free-plan service quota before creating a new service. Pin the new Preview
  origin in Neon's trusted-origin list only when necessary.
- A testable Preview deployment and successful CI are necessary but not
  sufficient to authorize live write tests. Keep all PRs draft/unmerged.
