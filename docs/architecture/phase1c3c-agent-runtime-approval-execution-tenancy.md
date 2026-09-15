# Phase 1C.3C — Agent Runtime, Approval, and Execution Tenant Isolation

## Problem

The agent subsystem was the last Critical cross-tenant surface in CXOps:

- `AgentRun` carried no `organization_id`; runs were loaded globally by
  `run_id`, listed globally, approved/rejected globally, and executed globally.
- `AgentWorkflowService._load_ticket` loaded tickets without tenant scope, so a
  user could analyze a foreign organization's ticket.
- Run payloads (response drafts, reasons, reviewer notes, tool plans) and event
  streams were readable from any tenant that knew a `run_id`.
- Approved agent execution could write to Zendesk — an external side effect —
  scoped only by whatever ticket the run happened to reference.

The exploit chain:

```
Org A user → guessed Org B AgentRun id → inspect / approve / reject / execute
```

was Critical: a guessed id could drive real external Zendesk mutations against
another tenant's customer.

## Trust chain (the phase's one-way gate)

Everything the agent runtime does flows through one persisted chain:

```
CurrentTenant
  → tenant-owned Ticket        (loaded by organization_id in SQL)
  → tenant-owned AgentRun      (organization_id copied from the persisted ticket)
  → tenant-owned RAG           (Phase 1C.3B: retrieval bound by organization_id)
  → tenant-owned integrations  (Phase 1C.3A: credential selected by organization_id)
  → safe agent execution
```

Two invariants anchor the design:

> **"An agent run can never outlive or escape its ticket's tenant boundary."**

> **"Model-generated tool arguments never choose the tenant."**

## Database ownership

`AgentRun` now has `organization_id` (FK → `organizations.id`, nullable in the
staged migration). The run/ticket ownership is **also** enforced by the
database, mirroring the knowledge document/chunk invariant:

- `tickets`: `UNIQUE (id, organization_id)` (`ux_tickets_id_organization_id`).
- `agent_runs`: composite FK
  `(ticket_id, organization_id) → tickets(id, organization_id)` with
  `ON DELETE CASCADE` (`fk_agent_runs_ticket_organization`), replacing the old
  single-column `ticket_id` FK.

A run that references an Org B ticket while claiming Org A ownership violates
the composite FK and cannot be inserted — this is the database gate for the
mismatch regression, not route discipline.

## Staged legacy migration

`1c3c0001` adds the nullable column, FK, index, then backfills **only**
deterministic, unambiguous ownership:

```sql
UPDATE agent_runs
SET organization_id = tickets.organization_id
FROM tickets
WHERE agent_runs.ticket_id = tickets.id
  AND tickets.organization_id IS NOT NULL;
```

Every run points at exactly one ticket row (primary key), and the ticket's
organization is a single persisted value, so this assignment is unambiguous.
Runs whose tickets are unowned (legacy NULL-org tickets) are left NULL and are
**inert**: they cannot be listed, fetched, approved, rejected, executed, or
streamed by any tenant path. The eventual `NOT NULL` transition is deferred
until an explicit cleanup of NULL rows.

`1c3c0002` creates the `tickets.id/org` composite unique target and swaps the
run FK to the composite, tenant-consistent constraint.

Both migrations have clean downgrades; `alembic check` is clean.

## Repository safety

`AgentRunRepository` now separates tenant surfaces from the worker-only path:

- Tenant-scoped (all bound by `run_id` **and** `organization_id`):
  `get_by_run_id_for_tenant`, `get_pending_run_for_tenant`,
  `list_runs_for_tenant`.
- INTERNAL-ONLY (explicitly `*_unscoped`, documented as worker-only):
  `get_by_run_id_unscoped`, `claim_for_execution_unscoped`. No route caller.
- Every state-transition method (`approve`, `reject`, `mark_*`, `mark_executed`,
  `mark_execution_failed`, …) calls `_assert_tenant_owned` and fails closed on a
  NULL-org run, so even an internal caller cannot transition a legacy run.

The routes never reference the old bare `get_by_run_id(`, `list_runs(`, or
`claim_for_execution(` (regression-guarded by `test_r`).

## Route tenancy

Every human/JWT agent route requires `CurrentTenant` and propagates
`tenant.organization_id` into the service/repository layer:

- `POST /agent/tickets/{id}/analyze` — ticket loaded with `organization_id`;
  foreign ticket id → non-enumerating 404; the run's ownership is copied from
  the trusted persisted ticket, and the client cannot choose it.
- `GET /agent/runs` — tenant-scoped listing.
- `POST /agent/runs/{id}/approve` / `reject` — tenant-scoped pending lookup
  first; reviewer note/subject applied only after ownership is proven. Foreign
  or missing run ids are indistinguishable (404 with the standard
  "was not found" template).
- `POST /agent/runs/{id}/execute` — tenant-scoped lookup; queueing only for the
  tenant's own run.
- `GET /agent/runs/{id}/events` — tenant-scoped; foreign run ids return the
  non-enumerating 404, so plan steps / drafts / notes / tool output / errors
  never leak.

## Execution tenancy (the sensitive path)

`execute` is only reachable from the durable job worker, which resolves the
organization from the **persisted job** (`IntegrationJob.organization_id`, set
at enqueue time from the trusted run — never from payload/headers/arguments).
`execute` then:

1. loads the run by id (internal unscoped lookup),
2. requires `run.organization_id == job.organization_id` (fail closed),
3. refuses NULL-org runs,
4. claims execution bound by run id **and** organization,
5. loads the ticket tenant-scoped and requires `ticket.organization_id ==
   run.organization_id`,
6. uses the run's persisted organization for every Zendesk credential lookup
   and mutation and for the post-write sync.

No external write can reach a credential selected by a foreign tenant, and a
queue job crafted with a wrong organization cannot claim a run outside it.

## Tool boundary

Tools are classified by the existing `ToolAuthorizationService` policy:

- READ sink: `zendesk.get_ticket_comments`, `zendesk.get_groups` (via
  `find_group_id`) — already per-org.
- WRITE/EXTERNAL sinks: `zendesk.update_ticket`, `zendesk.add_internal_note`,
  `zendesk.send_reply`, plus post-execution `ZendeskSyncService` sync — all
  invoked with `organization_id` derived from the trusted run/ticket.

No tool accepts an `organization_id` argument from the model. The persisted
tool-plan arguments (`team`, `priority`, `reason`, `body`, `response_draft`)
carry no tenant selector; the tenant is threaded by the executor from the
claimed run. This is the “model-generated tool arguments never choose the
tenant” invariant in code.

## RAG caller consistency

Phase 1C.3B made retrieval tenant-safe. The agent workflow's retrieval node
reads the organization from the **persisted, tenant-scoped ticket** loaded by
the graph — never from a prompt field — and calls
`KnowledgeSearchService.search(..., organization_id=<ticket org>)`. A foreign
run can never consume another org's knowledge because both the ticket lookup
and the run creation are tenant-bound before retrieval runs (`test_o`).

## Zendesk tenant propagation

Execution selects the Zendesk integration by `organization_id` (from the run =>
ticket, DB-consistent). `ZendeskClient.request` is the only credential-selection
choke point and takes `organization_id` on every call; the execution + sync
paths use the same resolved org. `test_p` proves the captured organization is
always the run's tenant and never the other org.

## Event stream

The polling events route (`GET /agent/runs/{id}/events`) resolves the run
tenant-scoped first. Foreign and missing ids yield the identical
non-enumerating 404 detail; owned runs return their event stream (`test_q`,
`test_b`).

## Legacy NULL-org runs

NULL-org runs are inert on every tenant surface and cannot be transitioned even
by internal repository calls (`test_k`). They remain inspectable only through
the explicitly unscoped worker tooling during migration cleanup.

## Security tests

`tests/test_agent_tenant.py` covers the full Phase 1C.3C test matrix A–R with
two synthetic organizations (Org A → `user-alpha`, Org B → `user-beta`), plus:

- a strong cross-tenant side-effect regression — Org A attempts to execute an
  Org B run: tenant-safe 404, zero Zendesk mutation calls, Org B's ticket and
  run untouched, no job queued, no run information disclosed;
- a database mismatch regression — an `AgentRun` whose `organization_id` is Org
  A referencing an Org B ticket fails the composite FK at commit (`test_j`).

The decision LLM and Zendesk network boundary are monkeypatched (deterministic
fakes), so the suite never contacts OpenAI or Zendesk.

## Backfill boundary

New tenant-facing ingest always requires a resolved `organization_id`; no code
path creates an unowned run for a tenant flow (`analyze` fails-closed if the
ticket org does not equal the resolved tenant org).

## Deferred (Phase 1C.3D)

- Observability tenancy (aggregate agent/ROI dashboards remain global-summary
  surfaces; deferred by phase scope).
- RBAC / per-role tool-policy authorization (existing tool-policy auto-approval
  is unchanged; this phase enforces tenant isolation only).
- Organization-switching UI (single-org Control Center unchanged; the
  `X-CXOps-Organization-ID` header stays a server-only selector).