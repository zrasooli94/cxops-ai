# CXOps AI — customer chatbot development track

**20 October 2026 · Source-only / draft / not deployed**

## Why this track is separate

The Render worker series (#44–#49) validates scheduler reliability,
PostgreSQL claims, crash recovery, resource usage and duplicate-side-effect
mitigations. It does not measure whether the customer chatbot answers useful
questions or offers a good first impression.

The chatbot already has an embeddable iframe/loader, tenant-aware sessions,
an asynchronous AI turn dispatcher, tenant-scoped knowledge/RAG eligibility,
staff handoff/inbox, replay-resistant messaging, theme palettes and role-based
configuration. It is not a blank-slate implementation. By default, substantive
agent decisions still require human approval; only a tenant explicitly enabled
for grounded auto-replies can publish qualifying source-backed information
responses without staff review.

We can improve the chatbot's customer experience in **independent draft
branches based on master** while the Render production cutover remains HOLD.

## Milestone C1 — guided first-contact interaction

### What changes

- The initial open conversation offers two fixed, tenant-neutral starter
  prompts: **Ask about the business** and **Get help with an issue**.
- Choosing a prompt **only fills** the editable message input and focuses it;
  the customer must press Send (or Enter) before any message, AI request,
  ticket processing or resource usage occurs.
- Starter suggestions disappear after the first inbound visitor message, if
  the user starts typing, while a send/reply is pending, or when the session
  is connecting, in error, closed, or handed to a human. Restored conversations
  with prior visitor messages do not look like a fresh AI session.
- The tenant's existing maximum-message-length bound filters suggestions.
  No prompt is offered if it would exceed the configured cap.
- Existing widget themes, human-support button, origin allowlist, bearer
  session token, role separation, history polling and conversation status
  remain unchanged. No new public endpoint, environment variable, API secret,
  database migration or model permission.
- Copy explicitly says suggestions never send automatically. It does not
  promise a response time or claim staff are online.

### Verification contract

Dedicated read-only GitHub Actions CI runs **only for the draft PR targeting
the chatbot source-only base branch**, not the production master build:
`npm ci`, frontend Node tests (including new starter helper tests), ESLint
on changed files, Next.js route type generation, TypeScript typecheck and a
Next.js production build **in the CI runner**, not on Vercel/Render production.
Do not use OpenAI, Zendesk, email, production databases or billing secrets.

The helper tests verify authored message bounds and uniqueness, rejection of
invalid tenant length caps, suppression after a draft / visitor message /
pending work and after AI-to-human state changes. The actual integration
must be confirmed by TSX typecheck, lint and build plus human preview review:
unit helper tests alone are not end-to-end browser interaction tests.

### Explicit non-goals

This milestone does **not** change how AI facts are retrieved, enable
autonomous Zendesk/ticket writes, skip human approvals or make grounded
answers available by default. The starter texts do not claim to represent
any particular tenant's services. The existing human-request action is not
triggered by a starter.

## Next product milestones

**C2 — knowledge-backed answers and truthfulness:** review the current
tenant opt-in for grounded auto-replies, add an observable FAQ-answer testing
workflow against entirely synthetic organizational knowledge, verify unknown
questions decline/handoff, source boundaries, language behavior and no
cross-tenant citation. Do not call paid models without explicit authorization.
Support staff-approved knowledge ingestion and explicit "no source" behavior.

**C3 — professional visitor experience:** improve conversation status and
handoff visibility, keyboard/mobile accessibility, clear source attribution
where safely public, real browser-based preview testing, loading states and
staff continuity. Avoid response-time promises or exposing internal audit data.

**C4 — business operations:** ticket context, CRM/tool integrations and
approval flows. Real external writes remain behind independent per-provider
idempotency/security review and owner-authorized activation.

**C5 — commercial product:** self-service widget setup, usage/quality
dashboards, organization-level billing and plan limits, beta feedback,
onboarding and controlled release.

Every development PR remains source-only until an independently authorized
production release. The production GitHub worker is unchanged and the
isolated Render synthetic Cron service stays suspended. Existing Phase 1Y
ticket writes stay off. Repository visibility remains public.
