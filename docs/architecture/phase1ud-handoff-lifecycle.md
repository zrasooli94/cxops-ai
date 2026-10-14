# Phase 1U-D — Full customer-chat and human-handoff lifecycle certification

**Status:** Draft PR for disposable-DB CI testing. No runtime, schema, app-hosting,
AI-provider, external business integration, or notification setting changes.

## Scope and expected lifecycle

The preceding phases check specific endpoints and authorization in isolation.
This milestone composes them into a real multi-actor HTTP journey against
disposable PostgreSQL and a deterministic, mocked AI decision.

1. Create a tenant-scoped public widget and visitor session through HTTP.
2. Send the customer's initial question with a fake `no_action` AI decision.
   Confirm persisted inbound and fallback while status is `ai_active`.
3. Request staff twice through the anonymous customer endpoint; confirm
   idempotent `human_requested`, visible in the tenant's staff queue.
4. A viewer may read the queue but cannot assign. Authorized staff assigns,
   a repeated claim receives HTTP 409, and assignment identity is preserved.
5. A customer follow-up after assignment is stored once (client message
   idempotency) without calling an AI model, RAG, or a business tool.
6. Authorized staff releases (queue again), repeat release rejects, then staff
   reassigns and resolves the session. Repeated resolve is idempotent.
7. Verify linked ticket is `solved` with SLA resolution, linked conversation
   `closed`, persisted messages preserved, assignment trail survives close,
   closed sessions disappear from staff queue and reject new human requests.
8. Separately validate foreign staff cannot list, assign, release, resolve,
   read business actions or forge a selector for another tenant.
9. Exercise two simultaneous staff claims; one must win and the other must
   receive a conflict. Also verify wrong-origin/invalid-token requests are
   refused with no DB mutation.

## Security and infrastructure boundaries

- In-process ASGI HTTP calls only; test organizations and tokens are synthetic.
  Reuse existing per-test org namespace and row cleanup.
- Actual AI decision and search logic are stubbed. After handoff an explicit
  forbidden-AI sentinel makes any new AI turn fail the test.
- `HANDOFF_NOTIFICATIONS_ENABLED=false`: no Apprise import or outbound
  notification, and no `handoff.notify` jobs staged.
- Existing durable job, membership, tenant checks and ticket/close lifecycle
  are unchanged. No mail, Telegram, Nhost, Zendesk or OpenAI network calls.
- Run under the existing **Backend Tests (Unit)** job with its disposable
  PostgreSQL/pgvector service. Do not target Neon.
- No additional app dependencies, migrations or paid hosting.

## Release conditions

- New lifecycle tests green individually and in the full backend suite.
- Existing Schemathesis anonymous/staff contract tests, Ruff, mypy, Docker,
  frontend and gitleaks all green on the final feature commit.
- Review any genuine lifecycle or tenant-isolation defect separately;
  do not weaken assertions or silently modify production behavior.
- Merge only after CI and an independent review of the complete lifecycle.
