# Phase 1U-A — feature-disabled Apprise staff notifications

**Status:** implementation proposal / draft PR. **No production activation**.
Baseline: master e29ceaa (Phase 1T-C). Provider transport: Apprise 2.0.1,
restricted to one approved RISPU email and Telegram destination.

## Findings from the read-only independent assessment

- Initial handoff originates at exactly four sites in
  `app/services/public_chat_service.py`: explicit request, AI/RAG fallback,
  exhausted AI allowance and permanently failed async turn.
- All now call `PublicChatRepository.handoff_to_human`, which uses an
  organization-bound compare-and-set and stages an outbox job **before the same
  commit**. Concurrent retries cannot create duplicate initial jobs.
- The existing `integration_jobs` state machine, claim fencing, advisory
  leases, retries, and API-side turn dispatcher are reused. No additional
  background service or Redis is required.
- A second instance of the same dispatcher runs only with the feature ON,
  globally bounded at 2 and per-tenant at 1. Its startup recovery is paired
  with a small reconciler for post-activation, still-waiting initial handoffs.
  The existing GitHub Actions worker remains the 5-minute backstop.
- The backstop installs `requirements-lock.txt` and receives matching
  rollout settings/secrets, not logged. A worker that has no rollout flag
  **defers** without consuming an attempt; an explicit OFF suppresses pending
  notifications.
- One job key per initial handoff, exactly
  `handoff-notify:v1:{organization_id}:{session_id}:initial`. One job tracks
  provider acceptance for both channels in bounded payload metadata. Customer
  questions, token hashes, provider URLs, email addresses, and bot tokens
  are never persisted in that payload.
- The network adapter permits only `mailtos` (TLS SMTP, fixed SMTP host
  allowlist) and `tgram` with a single numeric destination. Hostile Apprise
  schemes, arbitrary HTTP/form, config includes, attachments, redirects and
  local endpoints are forbidden. Every URL forces `cto=2&rto=4`.
- Apprise async_notify delegates blocking sends to worker threads. Cancelling
  the asyncio task does not stop the underlying send. There is no claim of
  exactly-once delivery; duplicate alert *attempts* can happen across retries.
- A six-initial-alert-per-organization/per-hour fixed bucket caps both
  destinations. Additional alert jobs are suppressed and one coalesced digest
  per hour summarizes the number of excess requests (no customer content).
  Session state and tenant eligibility are always checked at execution time.
- `provider_accepted` and bounded `suppressed:<reason>` outcomes go in
  job JSON, not in an unreviewed database migration.
- No customer message, recipient, token, raw exception or credential URL
  is written to logs or outbound message templates.

## Phase 1U-A review corrections (overnight hardening)

- **Tests**: the rollback test now proves atomicity through an independent
  connection with captured primary keys; the flood test uses an HTTPS fake
  frontend (production HTTPS validation unchanged) and asserts six per channel
  and a digest that is neither sent nor due inside its hour.
- **Atomicity**: `handoff_to_human` rolls back on any failure, so the status
  change and its outbox row commit together or not at all; a not-yet-active
  rollout no longer raises into the chat path.
- **Digest accuracy**: suppressed jobs record the hour in which the cap was
  hit (`digest_hour`); the digest counts by that, not by row creation time.
- **Apprise v2.0.1 inspected directly** (real plugins, mocked sockets):
  Telegram posts only to `api.telegram.org` with TLS verification and a
  `(2.0, 4.0)` timeout; e-mail uses an allowlisted SMTP host with STARTTLS and
  a 2.0 s socket timeout. A hostile `cto`/`rto` in the URL cannot enlarge the
  deadlines (they are overwritten). Recipient lists, display names, header
  injection and every option outside the allowlist are rejected before
  Apprise parses the URL; the recipient pattern no longer accepts a comma
  list (Apprise only happened to drop the second entry).
- **Credential exposure found and fixed**: at DEBUG Apprise logs the Telegram
  bot token (the request URL) and raw provider error text. Its logger is now
  muted and detached from the root handlers; tested with the root logger at
  DEBUG that neither token, password nor recipient reaches any log or
  exception chain.
- **Not natively async**: `async_notify` runs blocking sends in Apprise's own
  thread pool and a running send cannot be interrupted. Deadlines therefore come
  from the socket timeouts, and concurrency is bounded (2 global, 1 per tenant,
  32 pending submissions; excess jobs stay durable).
- **Connections**: a provider call holds one dedicated advisory-lease
  connection per job (the session's pooled connection is released first).
  `AnalysisCapacity` reserves two connections for them, only when the feature
  is ON.
- **Shutdown**: the notification dispatcher waits, then cancels what is left; a
  cancelled job keeps its claim and is recovered like a crashed one.

## Required flags (unset/false by default)

- `HANDOFF_NOTIFICATIONS_ENABLED=false`
- `HANDOFF_NOTIFICATION_PILOT_TENANT=rispu`
- `HANDOFF_NOTIFICATION_ACTIVATED_AT=` (owner chooses timestamp at rollout)
- `HANDOFF_NOTIFICATION_EMAIL_URL=` (secret, one approved staff email)
- `HANDOFF_NOTIFICATION_TELEGRAM_URL=` (secret, one approved Telegram chat)
- `HANDOFF_NOTIFICATION_MAX_PER_HOUR=6` (1-6)
- `HANDOFF_NOTIFICATION_CHANNELS=email,telegram` (non-empty subset; every runner
  needs a valid destination for each listed channel, or it defers)

An unset GitHub Actions variable arrives as an empty string and means "the
default". An invalid combination (enabled without an activation time, another
tenant, a cap outside 1-6, an unknown channel) **downgrades to OFF and logs the
reason at start-up**; it never raises, so a mistyped notification variable
cannot stop chat, SLA or job processing in any process.

*Malformed values are handled before field parsing.* A `mode="before"` model
validator parses each notification field with a throwaway adapter; an
unparseable flag (`maybe`), activation time (`not-a-date`) or cap (`invalid`,
`6.5`) is replaced by its safe default, the feature is forced OFF, and
`handoff_notification_config_error` records the field names only
(`unparseable notification setting(s): ...`), never the value (which could be a
credential-bearing URL). The same holds when the flag is explicitly OFF. The
derived error field cannot be injected from the environment.

*One flag vocabulary.* The delivery gate interprets
`HANDOFF_NOTIFICATIONS_ENABLED` with the same parser as `Settings`
(`parse_handoff_flag`; pydantic accepts yes/no/t/f/y/n as well as
true/false/1/0/on/off). Previously a worker judged `no` as "missing" and
deferred forever while `Settings` read it as OFF. Now: parseable false =>
`suppressed:disabled`; parseable true with a valid rollout => send; empty,
unparseable or invalid rollout => defer.

All outbound sends are blocked while the main feature flag is false. The API
and GitHub Actions worker must use consistent rollout config and secrets.

## Security and product boundaries

- The staff Handoff Queue remains the source of truth. Alerts do not assign,
  close or reactivate sessions and never authorize business tools.
- Notification text includes only a generic handoff headline and the HTTPS
  URL of the authenticated staff queue.
- The two destinations must be independently validated as staff-owned.
  `organization_memberships.email` is display-only and cannot authorize a
  recipient. Never use visitor-provided URLs.
- The user-requested first release does not expose new API or frontend settings
  routes and does not touch Nhost, Neon roles or customer records.
- This is a staged, default-OFF implementation; **live sending still requires
  separate owner approval, a production-ready worker, and green CI**.

## Failure, delivery and expiry behavior

- If feature flag explicitly OFF: `suppressed:disabled`.
- If role/tenant inactive: `suppressed:inactive`.
- If session is closed, already assigned, or expired: `suppressed:stale`.
- If a required destination is missing **or invalid** on the runner:
  `JobDeferredError` (no attempt burned, nothing sent, no channel sent alone,
  no fallback). The alert stays durable and goes out once the secret is
  corrected; a stale alert is then suppressed by the session check.
- If the worker flag/secrets are absent: the same deferral. An explicit
  `false` is the only state that suppresses (`suppressed:disabled`).
- If 6/hour budget exhausted: `suppressed:rate_limited`, one digest queued
  after the current hour; a digest states the number of excess *requests*
  recorded, not the number of still-open conversations.
- Provider timeout/rejection: retry with bounded backoff; never block chat.
- Channel-specific provider acceptance is persisted before attempting the
  next channel so partial retries avoid re-sending already accepted channels
  where possible. A crash after provider acceptance but before the commit may
  still duplicate.
- Job status is authoritative (`pending/processing/retry/completed/failed`);
  payload fields carry supplemental internal outcomes only.

## Release gates

1. Dependency resolution, full backend/frontend/secret-scan CI green.
2. Disposable PostgreSQL tests: all four handoff sites, transition concurrency,
   crash-window rollback, tenant negatives, 120/hour flood and digest, kill
   switch, per-channel partial retry, worker config drift and restart recovery.
3. No outbound requests in CI. Provider mocked at the adapter boundary.
4. Memory/boot benchmark on Render Starter image; no material regression.
5. Code review confirms that feature remains OFF in production.
6. Owner separately verifies the two staff destinations before an activated
   RISPU-only pilot; no real messages or infrastructure changes without approval.

## Known limitations

- The existing five-minute GitHub Actions worker can be delayed, so recovery
  is best-effort, not a response-time SLA.
- The existing job repository uses a 600-second stale claim threshold for the
  scheduled worker. The API dispatcher can recover notification claims faster.
- Apprise cannot guarantee that an email or a Telegram message was read.
- Neon Free can be woken by the five-minute cron schedule, increasing compute
  time; assess actual usage before expanding or increasing polling. The
  notification feature adds no polling of its own.
- At-least-once: if the process dies after a provider accepted a message and
  before that acceptance is committed, a retry can send the same alert again.
  Exactly-once delivery is not claimed.
- A cancelled attempt can still deliver after its job is marked for retry
  (Apprise sends cannot be interrupted); this is the same duplicate window.
