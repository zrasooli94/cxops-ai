# Phase 1M — Automotive Service Pilot (A1) Architecture

The A1 Automotive Pilot is a self-contained, fully synthetic demo tenant that
exercises the platform's existing service, agent, knowledge and evaluation
infrastructure through a dealership-shaped scenario catalog — without adding
any automotive-specific columns, logic or migrations to the generic platform.

## Goals

- Provide a complete, realistic demo organization ("A1 Automotive Pilot
  (Demo)") that can be seeded idempotently into any environment.
- Cover every vehicle-acquisition service step and the defined auto-parts
  scenarios with a bounded 18-scenario evaluation catalog (14–18 allowed).
- Validate automotive agent and RAG evaluation datasets with the existing
  Phase 1J/1K schemas — without weakening any threshold.
- Add a static control-center showroom page and developer documentation with
  zero new backend surface.
- Keep the generic platform generic: no automotive columns, no DB migration,
  no channel enum additions, and no fabricated AI outcomes.

## Tenant Design

The pilot is an ordinary `Organization` distinguished only by its exact name
`A1 Automotive Pilot (Demo)`, an `external_id` of
`a1-automotive-pilot-demo`, and an `industry` of "Automotive Services".

| Dimension | Definition |
| --- | --- |
| Service lines | Vehicle Acquisition, Auto Parts, General Support |
| SLA policies | A1 Vehicle Acquisition SLA (default), A1 Auto Parts SLA, A1 General Support SLA |
| Queues | 6 (valuation, pickup, documents/payments, parts sales, parts returns/warranty, general) |
| Customers | 6 fictional contacts, all `@example.com`, fictional 555 phones |
| Knowledge pack | 8 pilot documents, each carrying the explicit non-guidance disclaimer |
| Tickets | 40 (18 scenario tickets + 22 volume tickets) across the current and previous windows |
| Conversations | 1 local conversation per ticket (`provider="cxops"`, `external_thread_id=NULL`) |

Everything a seeded ticket needs — SLA deadlines, responses, escalations,
resolutions — is derived deterministically from the ticket spec, so re-running
the seed against the same reference time is a no-op.

## Files

| Path | Role |
| --- | --- |
| `scripts/automotive_pilot_data.py` | Single source of truth: tenant/SLA/queue/customer/knowledge/scenario/ticket constants, eval loaders, `validate_catalog()` |
| `scripts/seed_automotive_pilot.py` | Idempotent, tenant-explicit seed function + CLI |
| `scripts/run_automotive_pilot.py` | Optional safe replay of the real agent workflow |
| `evals/automotive_agent_cases.json` | 18 Phase 1J/1K-format agent cases (via `ticket_ref`) |
| `evals/automotive_rag_cases.json` | 10 RAG cases incl. two `should_refuse` guardrails |
| `frontend/src/lib/pilot/automotive-pilot.ts` | Static typed showroom config |
| `frontend/src/app/(control-center)/automotive-pilot/{layout,page}.tsx` | Guarded static showroom page + route metadata |
| `tests/test_automotive_pilot.py` | Seeder idempotency, tenant isolation, data safety tests |
| `tests/test_automotive_pilot_eval.py` | Eval dataset schema + coverage validation |

## Seeding Rules

- **Idempotent**: every row is looked up by its deterministic natural key
  (organization name, queue key, customer email, ticket external id, message
  dedupe key, escalation event key) before insert.
- **Tenant-explicit**: `seed()` creates only the pilot organization and only
  by its exact name. It never scans, adopts or modifies other tenants.
- **Local & fictional**: conversations are `provider="cxops"` with no external
  thread ids; customer emails end in `@example.com`; no real VINs, PII, or
  third-party credentials are seeded.
- **No fabricated outcomes**: seed creates operational rows only. It never
  writes `AgentRun`, `AIRequestLog`, or evaluation results.

## Optional Replay

`scripts/run_automotive_pilot.py` exercises the production
`agent_workflow_service.analyze(...)` over a small opt-in ticket set
(`valuations-001`, `documents-001`, `parts-sales-001`, `general-004`) only.
It resolves the organization by exact pilot name, uses
`allow_auto_queue=False` unless the operator explicitly passes
`--allow-auto-queue`, and keeps approvals/authorization untouched. A `--dry-run`
mode resolves ticket ids without running the workflow.

## Evaluation Datasets

The agent cases use the Phase 1J template (`name`/`subject`/`description` +
`expected_*`) but reference seeded tickets via a `ticket_ref` string instead of
a `ticket_id`; tests resolve refs to real ids and pass the results through
`EvalAgentCaseInput` so the existing validation (extra-forbid, bounded
specialist labels, unique specialists) still applies. The RAG cases mirror
`evals/rag_cases.json`. Only `documents-001` allows auto-execution (an internal
note); every other case requires human review or a read-only response.

## Frontend

`/automotive-pilot` is a server component that renders the static config with
zero fetching. It reuses the control-center `CapabilityRouteGuard` and
`ROUTE_REQUIREMENTS["/automotive-pilot"] = TICKET_READ`, sits in
`PRIMARY_NAVIGATION` directly after Transformation, and uses a new `car`
navigation icon (extended `NavigationIconName` + `navigation-icon.tsx`).

## Safety & Guards

The seed tests assert tenant isolation with decoy organizations, verify
idempotency, prove no fabricated AI rows, bound every escalation to its
reference time (Phase 1L bound), and keep knowledge ingestion offline through
deterministic 1536-dimension embedding stubs matching the pgvector column.

## Verification

| Gate | Command / expectation |
| --- | --- |
| Backend tests | `pytest tests -m "not integration"` (Phase 1M files + full suite) |
| Ruff | `ruff check --isolated` on Phase 1M Python files (repo config excludes app/tests/scripts/evals) |
| Alembic | single head `1j0a0003`, no migration |
| Frontend lib | `npm test`, `npm run test:auth`, `npm run test:session` |
| Frontend quality | `npm run lint`, `npx next typegen`, `npx tsc --noEmit`, `npm run build` |