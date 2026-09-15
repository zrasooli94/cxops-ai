# Phase 1C.3D — Observability, AI Request Logs, Metrics, and Background-Job Tenant Isolation

## Objective

Final backend tenant-isolation pass. Every tenant-owned AI/agent request, usage
record, job, metric, and aggregate must resolve to exactly one organization.
This closes the last finding that let an Org A authenticated user infer Org B
usage/cost/ROI/run/action patterns from the observability dashboard.

Phrases below that carry contractual force:

- **Observability is tenant-owned data.**
- **Aggregation never removes the tenant predicate.**
- **Platform-wide observability requires future privileged RBAC and is not
  exposed through tenant APIs.**
- **Legacy NULL-org telemetry is inert.**

## Trusted ownership sources

| Source | Trust level | Used for |
| --- | --- | --- |
| `CurrentTenant` (resolved from membership; selector never trusted on its own) | trusted | every human/JWT observability route |
| persisted `Ticket.organization_id` | trusted | automation event flow |
| persisted `AgentRun.organization_id` | trusted | run-level aggregation, ROI |
| persisted `IntegrationJob.organization_id` | trusted | job metrics |
| persisted `AIRequestLog.organization_id` | trusted | telemetry aggregation |
| subject / browser org id / JWT org claims / model-generated org args | **never** | observability authorization |

## AIRequestLog tenant ownership

`AIRequestLog.organization_id` (`FK → organizations.id`, nullable, staged).

- New tenant-owned logs **always** set `organization_id`:
  - `AIObservabilityService.record(...)` now requires keyword-only
    `organization_id: int`.
  - RAG `rag_service.answer` (4 sites) passes the caller's tenant.
  - Agent workflow `agent_workflow_service.analyze` (`feature="agent_decision"`,
    `request_id=f"agent-{run_id}"`) passes the run's tenant.
- Every human path that can originate a log (`/knowledge/...`, `/agent/...`)
  requires `CurrentTenant` and threads `tenant.organization_id`.
- No write path infers tenant from payload, subject, or model output.

### Parent-tenant consistency

`AIRequestLog` has no single stable tenant-owned parent for all log types
(RAG rows are ticketless) — a polymorphic FK would be over-engineered. The
product decision: **`organization_id` on the log is the authoritative ownership
field**, and the agent-decision rows are additionally correlated to
`AgentRun` via `request_id = 'agent-' || run_id`. The ROI join binds **both**
sides in SQL (`AgentRun.organization_id == :org AND
AIRequestLog.organization_id == :org`), so drift cannot silently leak.

### Backfill

Only the provably owned subset is backfilled (migration `1c3d0001`):

```sql
UPDATE ai_request_logs
SET organization_id = agent_runs.organization_id
FROM agent_runs
WHERE ai_request_logs.feature = 'agent_decision'
  AND ai_request_logs.request_id = 'agent-' || agent_runs.run_id
  AND agent_runs.organization_id IS NOT NULL;
```

RAG rows have no deterministic parent → stay NULL (inert). No arbitrary legacy
assignment. Later NOT-NULL is deferred to a phase with a data policy.

## Log write propagation

| Path | Organization source |
| --- | --- |
| RAG answer (low-context, no-results, ungrounded, grounded) | `CurrentTenant.organization_id` → `rag_service.answer` |
| Agent decision (`agent_workflow_service.analyze`) | run's persisted ticket/org chain |
| RAG evaluation (`rag_evaluation_service`) | `evaluate_case` requires `organization_id`; `_get_request_log` binds log `organization_id` |

Offline/system-level evaluation is not exposed through tenant APIs and is
documented as separate from tenant runtime.

## Observability routes: every endpoint requires `CurrentTenant`

`/observability/ai/summary`, `/observability/agent/summary`,
`/observability/ai/by-feature`, `/observability/agent/kpis`,
`/observability/agent/roi` — all switched from `CurrentPrincipal` to
`CurrentTenant` and call services with `tenant.organization_id`. Authentication
alone is insufficient.

## Aggregation tenancy (all in SQL)

Every aggregate in `AIObservabilityService` and `AgentObservabilityService`
includes `WHERE organization_id = :tenant_org` directly in the SQL statement:

- `COUNT` totals and feature/model breakdown groups
- `SUM`/`AVG` tokens, cost, latency
- success/grounded rate numerators/denominators
- run/action/status distributions
- approval/execution/auto-approval counters
- integration-job totals, statuses, retries, exhaustions, attempts
- ROI instrumented numerator, autonomous numerator, cost sum

No global aggregate is computed and filtered in Python. No aggregate is cached
and reused across tenants.

### ROI / cost tenancy

ROI joins `AgentRun` to `AIRequestLog` on
`request_id = concat('agent-', run_id)` with
`tenant_condition = (AgentRun.organization_id==org, AIRequestLog.organization_id==org)`
applied to the instrumented count, the autonomous-executed count, and the AI
cost sum. Both numerator and denominator are Org A rows only — the forbidden
"Org A savings / all-platform tickets" is structurally impossible here.

### Time-range / filter safety

Observability filters (window/status/model) are secondary predicates. The
tenant predicate is mandatory and cannot be overridden by any client-supplied
filter. Client filters never broaden tenant scope.

## Detail / drilldown security

The observability surface is aggregate-only; there is no
`/observability/requests/{id}` or `/observability/runs/{id}` detail endpoint to
leak prompts, drafts, reviewer notes, tool payloads, tokens, or cost. 404 is
returned for any non-existent sub-path. Any future detail endpoint must bind
`resource id AND organization_id` (non-enumerating 404 for foreign IDs).

## Integration job tenancy review

- `enqueue_agent_execution` / status surfaces are tenant-route gated (1C.3C).
- Worker-only globals are renamed and INTERNAL-only:
  - `get_by_id` → `get_by_id_unscoped`
  - `claim_next` → `claim_next_unscoped`
  - internal `mark_completed`/`mark_failed` call `get_by_id_unscoped`
- `scripts/worker.py` calls `claim_next_unscoped`.
- Worker claims are global **by necessity**, but every execution re-validates
  the job's persisted `organization_id` and downstream calls use
  `job.organization_id`; no payload field can switch tenant. No public route
  calls these.

## Automation unscoped-read decision

`AutomationService.process_ticket_event` no longer uses an unscoped ticket
read. Signature is now `process_ticket_event(db, *, event, organization_id)`:

- `organization_id is None` → fail closed (event marked processed, no rules).
- ticket read is `get_by_id_for_tenant(ticket_id, organization_id)`; missing →
  fail closed.
- rule match and update already tenant-scoped.

Callers:

- `zendesk_webhook_service` passes its verified `organization_id`.
- `webhook_service.receive_ticket_event` derives the org via a **single
  bounded `get_by_id_unscoped(ticket_id)` at the signed edge** (event body is
  signature-verified, ticket id is a server-persisted reference), then passes
  it to the fully tenant-scoped automation pipeline. Every subsequent rule
  read, rule match, and ticket update is tenant-scoped; a missing or NULL-org
  ticket fails closed. This retains the one deliberate unscoped hop at an
  authenticated/signed boundary and documents why it is safe.

## Legacy NULL-org telemetry

Rows with `organization_id IS NULL` (legacy `AIRequestLog`, `AgentRun`,
`IntegrationJob`) are excluded by every tenant aggregate via the mandatory
`WHERE organization_id = :tenant_org`. They contribute nothing to counts,
costs, ROI, latency, actions, failures, charts, or drilldowns. There is no
"global tenant" fallback.

## /metrics endpoint classification

Category **B — infrastructure metrics**. It remains unauthenticated by design
so Prometheus/ops tooling can scrape it; it is documented as protected by
deployment/network access controls. Prometheus label sets contain only
`action`, `decision_path`, `token_type`, `result`, `job_type` — never org
names, subjects, ticket IDs, customer IDs, prompts, tokens, or secrets.

## Indexes

- `ix_ai_request_logs_organization_id` — mandatory equality predicate on the
  new tenant column for every aggregation.
- `ix_ai_request_logs_organization_id_created_at` — composite for the
  time-window dashboard query shape (`WHERE organization_id = :org
  AND created_at >= :start AND created_at < :end`) so bucket scans stay
  tenant-local.

Both match real query filters; neither is speculative.

## Migrations

`alembic/versions/1c3d0001_ai_request_log_tenant_ownership.py`
(revision `1c3d0001`, down `1c3c0002`):

- nullable `ai_request_logs.organization_id` FK → `organizations.id`
- two justified indexes
- deterministic backfill of the `agent_decision` rows only
- clean downgrade (drops column + indexes)

Verified: `alembic upgrade head`, `alembic current` = `1c3d0001 (head)`,
`alembic check` = no new operations, downgrade `1c3c0002` → upgrade → head
roundtrip clean.

## Error semantics

| Case | HTTP |
| --- | --- |
| forged selector / no membership | 403 |
| ambiguous multi-membership, no selector | 409 |
| valid tenant with no telemetry | 200 with zeroed/empty aggregates (no global fallback) |
| foreign/nonexistent detail ID | non-enumerating 404 |
| invalid selector format | 403 |

## Security tests (`tests/test_observability_tenant.py`, 19 tests)

Seeds Org A (cost 0.031, tokens 4600, latency avg 150.0, 3 AI requests, 2
runs, 2 jobs) vs Org B (cost 20998, 2 runs, 2 jobs) plus NULL-org rows.

A summary counts only Org A · B token totals exclude Org B · C estimated cost
excludes Org B · D latency excludes Org B · E action counts exclude Org B ·
F success/failure rates exclude Org B · GH ROI numerator and denominator
exclude Org B · I by-feature/chart surface only Org A · J forged selector 403 ·
K no membership 403 · L multi-membership 409 · M selector switches exactly ·
N NULL-org telemetry inert · O no detail/drilldown surface · P no
`*_unscoped` callers in observability modules · Q predicate removal breaks
aggregation.

### Aggregation-leak regression

Org A cost 0.031 vs Org B cost 20998 → Org A summary is exactly **0.031**
(never 20998, never 20998.031). Same sensitivity for counts (3 vs global 6),
tokens (4600 vs 5650), latency (150.0 vs global ~95.83), runs (2 vs global 5),
actions (`respond: 1` vs global 3), and ROI numerator/denominator. Removing
the `organization_id` predicate fails these tests.

## Frontend

No tenant switcher. The existing one-org Control Center observability page
`frontend/src/app/(control-center)/observability/page.tsx` keeps working
unchanged; the BFF already transports the tenant selector; no authorization
state is persisted in `localStorage`; backend response shapes are unchanged.

## Verification results

- Backend: `pytest -m "not integration" -q` → 309 passed
- `ruff check app tests scripts alembic` → All checks passed
- `mypy app` → Success, 101 files
- Frontend: `test:auth` 66, `test:session` 21, `next typegen`, `tsc --noEmit`,
  `lint`, `build` all green
- `alembic check` clean, `alembic current` = `1c3d0001 (head)`, roundtrip clean
- `git diff --check` clean