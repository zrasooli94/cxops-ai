# Phase 1U-A review - Apprise staff handoff notifications (PR #25)

Reviewer stance: the implementation was treated as untrusted and re-verified
against `master` (e29ceaa). Labels: **VERIFIED** (executed in this review),
**INFERRED** (reasoned from code/vendor source, not executed), **NOT TESTED**.

## 1. Commits (branch `feat/phase1u-apprise-handoff-notifications`)

Pre-existing: `a5475b3` implementation, `e6578bb`, `106ebe6`, `72c81a0`,
`213c06c`, `2f2f7b8`. This review: `05c78ad` (CI test fixes, config can never
take a process down, digest-hour counting, channel set, metrics), `5eb499e`
(hardening tests, Apprise log silencing, strict recipient pattern, dispatcher
shutdown), `0f5fc4d` (invalid destinations defer, empty runner variables,
connection reserve), `f7848ac` (recovery tests, notes, Phase 1U-B assessment).
Final SHA and CI are recorded in the PR.

Test results (production lock dependencies, disposable PostgreSQL): full
backend suite **2779 passed** (clean run, no other DB activity); Phase 1U
focused suites 71 passed, repeated three times with no flakes; ruff clean;
mypy clean in all changed files (10 pre-existing errors in untouched files);
Alembic head `1q0a0001` (no migration); frontend lint, `tsc`, session (100)
and auth (625) tests pass. An intermediate full run showed two failures in
`test_phase1p9r_recovery.py` that were caused by this review's own concurrent
test run sharing the disposable database; both pass in isolation twice and in
the clean full run.

Changed files in the PR: `app/services/handoff_notification_service.py`,
`apprise_sender.py`, `public_chat_turn_dispatcher.py`,
`app/repositories/public_chat_repository.py`,
`app/services/public_chat_service.py`, `delivery_lease.py`,
`integration_job_service.py`, `app/core/config.py`, `metrics.py`,
`db_connection.py`, `app/main.py`,
`.github/workflows/production-worker.yml`, `requirements.txt`,
`requirements-lock.txt`, tests and the three documents under `docs/`.

## 2. The two CI failures

| Test | Root cause | Fix | Evidence |
|---|---|---|---|
| `test_atomic_rollback_when_outbox_staging_fails` | The test read `session.id` after the repository's rollback had expired the ORM row (`MissingGreenlet`) | Capture primary keys first; prove the result through an **independent connection**; assert the caller's session is usable again | VERIFIED: status stays `ai_active`, zero jobs |
| `test_flood_cap_suppresses_excess_and_creates_one_digest` | `frontend_base_url` defaults to `http://127.0.0.1`; production correctly requires HTTPS | The test supplies an HTTPS fake frontend; production validation untouched | VERIFIED: 6 per channel, 2 suppressed, 1 digest not due in its hour |

## 3. Atomicity and crash recovery

* **VERIFIED**: all four handoff paths (explicit request, AI/RAG/grounding
  fallback, exhausted allowance, failed async turn) stage exactly one job
  through the real service functions.
* **VERIFIED**: status flip and outbox row commit together or not at all;
  failure before commit rolls both back; 8 concurrent handoffs for one session
  create one job; a closed or human-assigned session is never changed or
  alerted; a foreign tenant id raises and creates nothing.
* **VERIFIED**: a failed dispatcher submission after commit leaves a durable
  pending job which the scheduled-worker path completes; API startup recovery
  re-offers pending jobs; staff release is not a new alert and is excluded from
  reconciliation.
* **FOUND AND FIXED**: a not-yet-active rollout timestamp raised into the
  customer's handoff path; invalid rollout settings raised inside `Settings`
  (a mistyped variable would have stopped every process). Both now degrade to
  "feature off" with a start-up log line.
* **INFERRED**: a process kill between provider acceptance and the commit of
  that acceptance can resend (documented at-least-once).

## 4. Tenant isolation

**VERIFIED**: stage and handler both require the pilot tenant by
`external_id` and an active lifecycle; the handler re-checks session,
configuration and organization identity; a job whose persisted tenant differs
is suppressed as `inactive` without a send; malformed or content-bearing
payloads are permanent failures; payload keys are limited to
`event, session_id, quota_granted, accepted_channels, outcome, digest_hour,
hour_start`. Recipients come only from server configuration, never from
`organization_memberships.email`.

## 5. Apprise 2.0.1 security review (real plugins, mocked sockets)

| Item | Result |
|---|---|
| Schemes | **VERIFIED** only `mailtos` and `tgram` pass; json/xml/form/windows/dbus/file/syslog/include/https rejected before Apprise parses |
| Telegram transport | **VERIFIED** fixed host `api.telegram.org`, TLS verified, `timeout=(2.0, 4.0)`, single chat id; any option beyond `cto/rto` rejected |
| E-mail transport | **VERIFIED** allowlisted SMTP host, port 587/465, STARTTLS/TLS, socket timeout 2.0 s, single recipient |
| Fan-out | **VERIFIED** comma/semicolon/space lists, display names, header-injection encodings, `cc/bcc`, duplicate `to`, attachments all rejected; recipient pattern tightened (Apprise had only dropped a bare second entry) |
| Timeout bypass | **VERIFIED** hostile `cto=9999&rto=9999` is overwritten; plugin objects report 2.0/4.0 |
| Credential exposure | **VERIFIED and FIXED** Apprise logged the bot token (request URL) and provider error text at DEBUG; its logger is now muted/detached; exception chains carry no secrets |
| SSRF / rebinding | **INFERRED** hosts are a fixed allowlist or `api.telegram.org`; IP literals rejected; no redirects are followed to attacker-chosen hosts by design, but name resolution uses the system resolver and a poisoned resolver is out of scope |
| Local-system plugins / config includes | **VERIFIED** unreachable (scheme allowlist; `AppriseConfig` never used) |

## 6. Timeout, concurrency and duplicate safety

* **VERIFIED**: Apprise `async_notify` is not natively async; sends run in its
  thread pool and cannot be interrupted. Deadlines come from socket timeouts;
  concurrency is two globally, one per tenant, 32 pending submissions.
* **VERIFIED**: a hung execution does not stall the event loop (max loop lag
  < 0.25 s in the test) and shutdown cancels what remains; the cancelled job
  keeps its claim for stale recovery.
* **VERIFIED**: two workers cannot send one job in parallel (advisory lease; the
  second defers without spending an attempt).
* **VERIFIED**: email accepted then Telegram failing retries only Telegram;
  acceptance is persisted per channel. A runner missing one required channel
  defers before sending either. A runner with the channel set restricted to
  `email` sends only email.
* **NOT TESTED**: a real network hang inside a live provider (sockets mocked).

## 7. Flood control and digest

**VERIFIED**: 120 initial handoffs executed by concurrent workers yield exactly
6 admitted jobs, 12 provider calls, 114 `suppressed:rate_limited` and **one**
digest per hour with `available_at` after the hour. After the hour elapses the
digest states "114 additional handoff requests" on both channels and is the
only extra send (7 per channel total). Retries of an admitted job do not
consume quota and cannot bypass the cap. **FOUND AND FIXED**: the digest used
row creation time; it now counts by the hour the suppression happened.

## 8. Worker and configuration parity

**VERIFIED**: the scheduled worker installs `requirements-lock.txt`
(cache key updated), receives the flag, activation time, channel set, cap and
both destinations (secrets via `secrets.`, flags via `vars.`). Missing variable
=> defers, no attempt consumed, nothing sent; explicit `false` =>
`suppressed:disabled`; invalid destination => defers (it previously consumed
the alert as "suppressed"). Unset GitHub variables arrive as empty strings and
now mean defaults.

## 9. Dependencies and resources

* **VERIFIED**: `requirements.txt` and `requirements-lock.txt` each resolve on
  Python 3.11 with `apprise 2.0.1`; `pip check` clean; new transitive packages
  `Markdown`, `oauthlib`, `requests-oauthlib` (`tzdata` is Windows-only). Both
  files are covered by the existing lock-parity test.
* **VERIFIED** in the production image under `--cpus 0.5 --memory 512m`
  (mocked transports): `import app.main` 2.6 s -> 2.5 s; peak RSS after import
  154 MB -> 154-156 MB; after provider warm-up 201 -> 201-203 MB; first
  Telegram send through real Apprise +0.13 s and peak RSS 213 MB (42 % of the
  limit). Apprise is not imported by the application at start-up (tested).
* **INFERRED**: the e-mail path loads one more plugin and is expected to cost a
  similar amount.

## 10. Remaining risks

| Rank | Risk |
|---|---|
| HIGH | None that block review. Live sending remains disabled and unapproved. |
| MEDIUM | Alert duplicates remain possible (at-least-once; non-interruptible sends). Staff have no acknowledgement path from the alert channel. |
| MEDIUM | The scheduled worker is a slow backstop (stale claim 600 s plus cron delay); worst-case recovery is tens of minutes if the API is down. |
| MEDIUM | Neon Free compute-hours are not changed by this feature, but the existing 5-minute cron can keep compute awake; usage should be checked before the pilot. |
| LOW | Digest counts requests, not open conversations. |
| LOW | Apprise has a large plugin surface; the allowlist is the control. A direct SMTP/HTTPS adapter would shrink it if a second channel type is never needed. |
| LOW | Rate-limit bucket is a fixed epoch hour (bursts at an hour boundary can send up to 12). |

## 11. Deliberately disabled

`HANDOFF_NOTIFICATIONS_ENABLED=false` (default), no destination configured, no
activation timestamp, no tenant other than RISPU, no settings UI, no
unauthenticated test-send endpoint, no new tables or migrations.

## 12. Recommended release and rollback

1. Merge with the feature OFF; deploy API (Render) and let the scheduled worker
   pick up the lock-file install. Verify chat, queue and worker unchanged.
2. Provision destination secrets on **both** Render and GitHub, plus the vars
   `HANDOFF_NOTIFICATIONS_ENABLED`, `HANDOFF_NOTIFICATION_ACTIVATED_AT`
   (GitHub variables must be set before Render to avoid deferral noise).
3. Owner-approved single synthetic handoff; confirm one job, per-channel
   acceptance in the payload, latency from the `staged` metric, no duplicate.
4. Rollback: set `HANDOFF_NOTIFICATIONS_ENABLED=false` on both runners. Queued
   jobs are suppressed (`disabled`), nothing is deleted, no schema change.

## 13. Follow-up: configuration parsing safety (after `9631a85`)

An independent review showed that the "downgrade to OFF" validator ran too
late: pydantic field parsing raises first. Reproduced on `9631a85`:
`HANDOFF_NOTIFICATIONS_ENABLED=maybe`,
`HANDOFF_NOTIFICATION_ACTIVATED_AT=not-a-date` and
`HANDOFF_NOTIFICATION_MAX_PER_HOUR=invalid` each made `Settings()` raise
(**VERIFIED**), which would have prevented the API, the scheduled worker and
scripts from starting.

* **Fix**: a `mode="before"` model validator parses only the notification
  fields, defaults what fails, forces OFF and records a field-name-only reason.
  Unrelated settings are untouched.
* **Related gap found and fixed**: the worker's delivery gate recognised only
  `true/false/1/0/on/off`, whereas `Settings` also accepts `yes/no/t/f/y/n`;
  `no` made a worker defer forever instead of suppressing. Both now share
  `parse_handoff_flag`.
* **VERIFIED** by `tests/test_phase1u_config_safety.py` (52 cases): all
  reported cases, the same garbage with the flag explicitly OFF, wrong Python
  types, a credential canary never echoed, `import app.main` and
  `import scripts.worker` succeed with the notification machinery unloaded,
  the production preflight does not traceback on them, `lifespan` logs the
  reason once and starts no notification task, explicit OFF suppresses,
  invalid/empty/missing defers (attempts unchanged), and **zero** provider
  calls in every case.
* **NOT TESTED**: a full `ENVIRONMENT=production` start with real secrets.


## Independent follow-up: blank-but-not-empty optional values

A further review found that the before-model sanitiser stripped whitespace to
test emptiness but left the original `"  "` value for Pydantic to parse.
Likewise, direct `None` for the boolean flag or integer cap would cause a
`ValidationError`. Both could still prevent optional notification configuration
from loading. The sanitiser now passes literal `""` to the existing
empty-means-default field validators, preserving the unchanged OFF default
without relaxing any non-notification setting. Regression cases explicitly
cover whitespace-only environment values, direct null inputs, active rollout
with a missing activation date, and a defaulted cap.

No merge, deployment, secrets change or outbound provider request was made
during this follow-up. Rerun full CI on the new draft-branch commit.
