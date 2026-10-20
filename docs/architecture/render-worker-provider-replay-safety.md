# CXOps AI — Provider acknowledgment/replay safety before Render Cron cutover

**20 October 2026 | Source-only engineering review | RELEASE HOLD**

This report is a **risk inventory and falsification test**, not provider certification
or permission to deploy. It builds on disposable process-SIGTERM readiness (PR
#47). Neither a successful SQL job completion nor a green disposable test
suite proves that an external service executed its effect exactly once.

## Observed source boundaries (review these exact paths again at release SHA)

| Boundary | Existing protection | Review outcome |
| --- | --- | --- |
| PostgreSQL integration jobs | `app/repositories/integration_job_repository.py`: durable claim, claim timestamp fencing, ten-minute stale recovery, max-attempt handling. | **SQL status/commit protected**, but a remote acknowledgment cannot join the SQL transaction. |
| Zendesk human public reply | `app/services/conversation_delivery_service.py`: stable `delivery_token` marker; paginated, fail-closed pre-send marker scan; `app/services/delivery_lease.py`: per-message advisory lease. `tests/test_zendesk_marker_scan.py` exercises several deep scan/ambiguous responses. | **Mitigation exists**, but needs review of provider consistency, visibility, partial errors, and actual platform failures. |
| Agent action to Zendesk | `app/services/agent_execution_service.py`: per-run comment marker; checks before plan replay. `zendesk.update_ticket` field updates use no comment marker, while comment-generating actions do. | **Review multi-tool / partial-plan replay explicitly.** A comment marker for one part of a plan must never certify unexecuted later actions. Check tool-plan cardinality and authorization digest before judging risk. |
| Zendesk webhook writeback | `app/services/zendesk_webhook_service.py` checks for its event marker in the `comments_response` returned by `ConversationIngestionService.ingest_zendesk_ticket`. Without a passed response, that ingestion calls `ZendeskClient.get_ticket_comments`, documented in `app/integrations/zendesk/client.py` as **one page (at most 100), oldest first** by default. | **Potential replay gap:** after a successful writeback and lost local acknowledgment, a marker outside the one fetched page need not be seen, allowing a duplicate private note. Requires a bounded, fail-closed, paginated lookup and a synthetic 100+ comments fault test before release. This is source-path inference, **not** an observed duplicate at Zendesk. |
| Staff handoff email/Telegram | `app/services/handoff_notification_service.py::_send_channels` sends via Apprise, then writes `accepted_channels` into PostgreSQL. It also has per-job advisory locking and multi-channel partial-retry coverage. | **Known ambiguity window:** provider may accept the message before `accepted_channels` is committed. Locks serialize concurrent senders but do not resolve an acceptance lost through a process crash. |
| Business-specific local tools | `app/integrations/a1_cash_for_cars/service.py` uses local persisted BusinessAction records rather than an outbound provider mutation. | Not equivalent to an external webhook/chat sender; separately assess any later integrations. |

## Executable risk reproduction in disposable GitHub Actions

`tests/disposable/worker_probe_cases.py::test_handoff_provider_acceptance_gap_replays_after_stale_recovery`
imports the **real** `_send_channels`, SQL claimant, claim aging/recovery and
per-job lease, while replacing Apprise's `send_staff_handoff` and URL
validator with safe in-memory functions (zero transport sockets/credentials).
It runs only after `require_disposable_environment` accepted the exact
GitHub Actions local PostgreSQL service.

1. Claim exactly one synthetic job in the disposable database (attempt 1).
2. Record acceptance in an inert fake `email` sender. Immediately inject
   `RuntimeError` before `_update_outcome(accepted_channels=...)` executes.
3. Assert exactly one **fake** provider acceptance, but no durable
   `accepted_channels` flag; the original job remains `processing`.
4. Confirm the normal worker cannot reclaim the row before it is stale.
5. Age only that row by 15 minutes, then re-claim via the real repository
   (attempt 2) and call the real `_send_channels` again, using the same
   fake provider. Assert the resulting **two fake acceptances** but one
   durable `email` acknowledgment.

**Expected test result is PASS when this counterexample is reproduced.**
A PASS explicitly means a **known at-least-once exposure was demonstrated**,
not that outbound sends are exactly once. Never aggregate that test into a
production-safe badge without displaying this separate release risk. The
failure window is deterministic fault injection, not proof that the external
service actually delivered duplicate messages in production.

## Release policies requiring explicit engineering/owner decision

### 1. Staff notifications: select delivery semantics, do not guess

No client-generated idempotency key is currently passed from
`_send_channels` into Apprise's transport API. The system cannot
transactionally commit remote acceptance with local PostgreSQL state.

Options for independent review:

- **At-least-once, duplicate-tolerant operational alerts:** explicitly
  accept that one staff notification can appear twice after a crash. Add
  human-readable stable reference and alert/reporting dedupe. Document
  customer/staff expectations and quantify impact.
- **At-most-once until manual reconciliation:** durably set a claim-fenced
  per-channel `send_unknown` state *before* the send; on crash/retry, stop
  automatic sends for that channel and require authenticated operator
  verification/reconciliation. This can lose/delay an alert if the provider
  never accepted it. Define timeout, audit records and manual repair first.
- **Provider-native idempotency/receipt lookup:** use only where the actual
  provider documents a stable idempotency key or retrievable unique
  acknowledgment, with explicit concurrency and error tests. Do not assume
  email/Telegram/Apprise offers this guarantee.

Do not silently change semantics or flip live notification flags in this
source-only branch. A real implementation needs dedicated review, tests,
operator UX and an owner policy decision.

### 2. Zendesk ticket comments and multi-step plans

Comment/note insertion is non-idempotent even when changing priority/status
is naturally repeatable. Implement a scoped and **bounded** provider marker
query for webhook writebacks, including deep pages, malformed cursors,
consistency lag and inconclusive scans that **must never authorize a second
write**. Preserve legitimate tenant and ticket identity. Do not reuse a
single positive comment marker to approve the entire multi-tool plan unless
a validated invariant proves exactly one externally mutating action per run.

Add fault cases:
- remote success + local crash/rollback before completion,
- marker appears on page 2+, old page contains no marker,
- marker pagination inconclusive or temporarily unavailable,
- concurrent workers with the same run/job after stale claim recovery,
- agent multi-tool plan with a partial first external write,
- authoritative provider confirmation vs ambiguous request timeout.

### 3. Separate the provider-safety and infrastructure gates

The existing synthetic Render Cron remains **SUSPENDED**. The first 24-hour
no-op scheduler observation was successful, and earlier source-only worker
load/SIGTERM tests passed, but these are **not** evidence for remote
idempotency or a `0.5c-512mb` Render container under realistic SDK load.

Before any production Cron activation or seven-day production shadow:

1. Close/review open replay risks above; human reviewer personally inspects
   the exact final source SHA and records a risk disposition per provider.
2. Independently authorize a Render-hosted **synthetic-only** resource canary,
   if necessary, with a purpose-built Render-only fail-closed runner and **no
   production database/provider secrets**. Do not bypass the GitHub-Actions-only
   local guard to run it on Render.
3. Measure platform process **and cgroup** peak memory on the proposed
   512 MiB instance, CPU/cold-start, SIGTERM grace, run delay and billed active
   time. Require a bounded 25-job/240s profile, a hard canary timeout and
   zero external-provider requests. Suggested resource **stop condition**:
   cgroup peak **>=384 MiB**, any OOM/overlap, or unexpected network/DB access.
   The 384 MiB criterion is an engineering reserve, not a Render service cap.
4. Maintain the production GitHub worker and the existing kill/rollback path
   unchanged until shadow safety and duplicate-side-effect semantics are
   independently reviewed. Require explicit owner approval for any paid
   provisioning, production secrets, merge, shadow execution or cutover.

**Disposition:** source-only investigation permitted; **provider replay
safety NOT CERTIFIED**. No production or paid service changes authorized.
