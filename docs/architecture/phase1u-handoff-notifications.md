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

## Required flags (unset/false by default)

- `HANDOFF_NOTIFICATIONS_ENABLED=false`
- `HANDOFF_NOTIFICATION_PILOT_TENANT=rispu`
- `HANDOFF_NOTIFICATION_ACTIVATED_AT=` (owner chooses timestamp at rollout)
- `HANDOFF_NOTIFICATION_EMAIL_URL=` (secret, one approved staff email)
- `HANDOFF_NOTIFICATION_TELEGRAM_URL=` (secret, one approved Telegram chat)
- `HANDOFF_NOTIFICATION_MAX_PER_HOUR=6`

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
- If destination malformed: `suppressed:unconfigured`, no fallbacks.
- If worker flag/secrets absent: `JobDeferredError`, no attempt burned.
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
  time; assess actual usage before expanding or increasing polling.
