# Phase 1O.1 — Transformation Experiment Model & Outcome Measurement Engine

## Scope

Phase 1O.1 delivers the **backend-first** Transformation Experimentation &
Outcome Validation milestone: a generic, tenant-owned experiment/pilot record
with a strict lifecycle, a deterministic outcome-comparison engine, and
persisted, non-causal measurement limitations.

This phase adds **no frontend workspace** (scheduled for Phase 1O.2), modifies
no Phase 1N table, and does not change Phase 1L analytics semantics. The
simulation engine (`service_transformation_simulation_engine`) is **never** used
to produce observed experiment results; observed outcomes come exclusively from
live tenant telemetry via the shared repository aggregates.

## Concepts

### Generic model, optional scenario link

An experiment is measurement/control-plane metadata only. It references the
change that was intended (hypothesis), the scope it applied to
(organization or a single tenant-owned queue), the windows used, the target,
and — after completion — the observed outcome and persisted comparison. A
`source_scenario_id` **optionally** links a Phase 1N scenario; the backend
resolves the trusted scenario row at create time and persists a bounded
`source_scenario_snapshot` (id, name, window_days, status, formula_version,
`projected_result`). Clients never supply projected values.

### Scope

- `organization` — a `NULL` `scope_key`; baseline and outcome cover the whole
  organization, and value/ROI metrics are available.
- `queue` — a required tenant-owned `scope_key`; value/ROI metrics are rejected
  at the schema layer because autonomous-execution economics are not
  attributable to a single queue.

Employee-level, customer, and agent scope are out of scope; no employee-level
scoring exists anywhere in this feature.

## Lifecycle

```
draft → (capture-baseline) → ready → (start) → running → (complete) → completed
  │        └─────────────┐         └───(cancel)─────────┘            │
  └─────(cancel)─────────┼───────────────────────────────┐           │
  └─────(archive)───────────(archive)──────────────────────(archive)──
```

Statuses: `draft`, `ready`, `running`, `completed`, `cancelled`, `archived`.

- **capture-baseline** — only from `draft`. Captures the trailing
  `baseline_window_days` snapshot from live telemetry, stamps
  `baseline_captured_at`, transitions to `ready`. The baseline is immutable; a
  replacement requires a new experiment.
- **start** — only from `ready`. Sets `actual_started_at`, transitions to
  `running`.
- **complete** — only from `running`. Measures the observed outcome over the
  actual run window `[actual_started_at, now]`, runs the comparison engine,
  persists `observed_outcome`, `outcome_comparison`, `measurement_status`,
  `comparison_version`, `actual_ended_at`, `measured_at`; transitions to
  `completed`.
- **cancel** — from `draft`, `ready`, or `running` → `cancelled`.
- **archive** — from `draft`, `ready`, `completed`, or `cancelled` →
  `archived`; already-archived is idempotent; **running cannot be archived**
  (complete or cancel first).

All transitions re-check status under `SELECT ... FOR UPDATE`
(`get_by_id_for_tenant_locked`) so concurrent transitions cannot race. Invalid
transitions yield a deterministic 409; tenant-missing rows are a generic 404.

## Data model

Alembic revision `c3f9a1d2b7e4` (down-revision `8520421279e1`) creates
`service_transformation_experiments` as a single head:

| Column | Purpose |
| --- | --- |
| `organization_id` | **NOT NULL** foreign key; the tenant boundary. |
| `hypothesis` / `target_metrics` | Operator-supplied JSONB, strictly validated. |
| `scope_type` / `scope_key` | `organization` needs `NULL` key; `queue` needs a key (check constraint). |
| `baseline_window_days` / `measurement_window_days` | 7/30/90 (check constraint). |
| `planned_start_at` / `planned_end_at` | Optional operator schedule. |
| `actual_started_at` / `actual_ended_at` | Written by start/complete. |
| `source_scenario_id` / `source_scenario_snapshot` | Optional Phase 1N link + bounded trusted snapshot. |
| `baseline_snapshot` / `baseline_captured_at` | Immutable live baseline. |
| `observed_outcome` / `outcome_comparison` | Measured outcome + persisted comparison (incl. limitations). |
| `measurement_status` / `comparison_version` | Integrity label + engine version. |
| `measured_at` | Completion timestamp. |
| `created_by_subject` | **Persisted** (unlike the known Phase 1N scenario-repo quirk; fix there stays separate). |

Named constraints and indexes; `organization_id` + `created_at` composite index
is `ix_service_transformation_experiments_org_id_created_at` (Postgres 63-char
identifier limit).

## Outcome comparison engine

`app/services/service_transformation_experiment_engine.py` is a pure,
side-effect-free function `compare_outcomes`. Deterministic for identical
inputs; mutates neither inputs nor module state.

Metric contract (fixed table):

- `autonomous_execution_rate`, `human_approval_rate`, `knowledge_usage_rate`,
  `reopen_rate` — rate kind, unit `percentage_points`.
- `total_sla_breaches` — count kind, unit `count`.
- `average_first_response_minutes`, `average_resolution_time_minutes` —
  minutes kind.
- `estimated_minutes_saved`, `estimated_net_savings_usd` — minutes/USD amount
  kinds.
- `roi_percent` — ROI kind, unit `percentage`.

For each targeted metric the comparison reports `baseline_value`,
`target_value`, `observed_value`, `change_from_baseline` (raw numerics,
percentage points for rates), `variance_from_target`, `projected_value`,
`variance_from_projection`, `unit`, neutral `direction_*` labels
(increased/decreased/unchanged/not_applicable), `measurement_status`, and an
explanatory `warning`. Values are **never inverted** for good/bad; the engine
never grades an outcome.

Projection variance uses a Phase 1N scenario's persisted `projected` result
only when the scenario was evaluated; a different source-window length than the
baseline window produces a deterministic warning and is **not** normalized.
`average_first_response_minutes` / `average_resolution_time_minutes` have no
scenario projection and are reported without one.

`measurement_status` precedence (highest first), computing an integrity label
never a verdict: `pricing_unavailable` > `insufficient_sample` >
`incomplete_window` > `no_observed_activity` > `measured`. `incomplete_window`
means the observed window is shorter than `measurement_window_days`;
partial-window results are flagged, never extrapolated.

## Baseline and observed measurement

Both snapshots share one builder
(`service_transformation_experiment_measurement.metrics_snapshot_for_window`)
over arbitrary `[start, end]` using the Phase 1L repository aggregates
(`ServiceTransformationRepository` window methods, each extended with an
optional `queue_id` filter for queue-scoped experiments):

- `tickets_resolved`, `reopened_tickets`, `first_responses`, `reopen_events`,
  `agent_runs`, `autonomous_executions`, `human_approval_required`,
  `knowledge_specialist_runs` (from `derive_specialist_path`), the four rates
  using the Phase 1L `_pct` rounding, and average first-response/resolution
  minutes.
- `first_response_sla_breaches`, `resolution_sla_breaches`,
  `total_sla_breaches` via
  `ServiceEscalationRepository.breached_counts_for_window` over the same
  explicit `[start, end]` window. Experiment SLA breach outcomes reuse the
  canonical Phase 1L persisted `ServiceEscalation` milestone semantics so
  observed outcomes are comparable with Phase 1N scenario projections.
- Value block (organization scope only) via
  `AgentObservabilityService.roi_summary(start, end)` with `roi_percent`.
  Queue scope carries a `None` value block.

The baseline snapshot for an experiment equals the shape used at capture; the
observed snapshot carries the elapsed (possibly partial) `window_days` so the
engine can flag a short window.

## Persisted limitations

`outcome_comparison.limitations` is persisted verbatim on every completed
experiment and always leads with:

> Observed improvement within an experiment window does not establish that the
> intervention caused the improvement.

Additional fixed limitations cover non-trial status, no statistical claim,
projection coverage, and (conditionally) source-scenario window mismatch /
not-evaluated, value unavailability, and incomplete window. The comparison
exposes no confidence score and no causal claim.

## API

`app/api/routes/service_transformation_experiments.py`, prefix
`/service-operations/experiments`:

- `POST ""` — create (manage) → 201
- `GET ""` — list (read)
- `GET /{id}` — get (read)
- `POST /{id}/capture-baseline`, `/start`, `/complete`, `/cancel`, `/archive`
  — manage

Errors: 400 invalid create payload/enum; 404 tenant-missing (generic, no
existence leak); 409 invalid transition; 403 missing capability; 401
unauthenticated. `organization_id` is never accepted from the body.

## RBAC

Two new capabilities in `app/core/rbac.py`:

- `transformation.experiment.read`
- `transformation.experiment.manage`

OWNER and ADMIN hold every capability; SUPERVISOR holds both experiment
capabilities; AGENT and VIEWER hold neither (experiment capabilities were
deliberately **not** added to `READ_ONLY_CAPABILITIES`).

## Tenancy

`organization_id` is resolved from `CurrentTenant` (`x-cxops-organization-id`
header) and enforced in SQL on every query. Queue and source-scenario lookups
are tenant-scoped, so cross-tenant identifiers fail with the same generic
message as missing ones. Experiment rows are strictly NOT NULL on
`organization_id`.

## Tests

`tests/test_service_transformation_experiment.py` covers model/constraints,
schema strictness, engine determinism + neutral-direction semantics,
top-level status precedence, lifecycle transition matrix, tenant isolation,
RBAC enforcement, live 1L baseline observation, value/ROI status handling,
projected-vs-observed distinction, and the causality guard (the required
disclaimer is present verbatim; no positive causal-claim phrasing appears).

## Files

- `app/models/service_transformation_experiment.py` (+ registration in
  `app/models/__init__.py`)
- `alembic/versions/c3f9a1d2b7e4_add_service_transformation_experiments.py`
- `app/schemas/service_transformation_experiment.py`
- `app/services/service_transformation_experiment_engine.py`
- `app/services/service_transformation_experiment_measurement.py`
- `app/services/service_transformation_experiment_service.py`
- `app/repositories/service_transformation_experiment_repository.py`
- `app/repositories/service_transformation_repository.py` (optional `queue_id`
  filters on window aggregates)
- `app/core/rbac.py` (experiment capabilities)
- `app/api/routes/service_transformation_experiments.py` (+ registration in
  `app/api/router.py`)
- `docs/architecture/phase1o-transformation-experiments.md`

## Rollout notes

Apply with `alembic upgrade head`; the revision is a single head off
`8520421279e1` and touches no Phase 1N table. No commit, push, or deploy was
performed as part of this milestone.

# Phase 1O.2 — Experiment Executive Workspace & Outcome Review

## Scope

Phase 1O.2 delivers the **frontend** for the Phase 1O.1 experiment API: the
`/experiments` library and the `/experiments/[experimentId]` outcome-review
workspace in the Control Center app.

The workspace is **presentation-only**. Every number rendered (baseline,
target, observed, deltas, projected values, value/ROI, limitations) is the
backend output from Phase 1O.1, transported verbatim through the existing BFF
proxy (`frontend/src/app/api/backend/[...path]/route.ts`). The frontend never
recomputes a comparison, a percentage-point difference, a net-savings figure,
or an ROI, and it never renders ranking, verdict, or causal-claim wording.

## Routes and authorization

- `frontend/src/app/(control-center)/experiments/layout.tsx` — page metadata
  (title "Transformation Experiments") and a `CapabilityRouteGuard` keyed on
  `ROUTE_REQUIREMENTS["/experiments"]` →
  `Capability.TRANSFORMATION_EXPERIMENT_READ`.
- `frontend/src/app/(control-center)/experiments/page.tsx` — library with
  filters (All / Active / Completed / Cancelled / Archived), the create form,
  and row lifecycle actions for managers.
- `frontend/src/app/(control-center)/experiments/[experimentId]/page.tsx` —
  outcome-review workspace (lifecycle, executive summary, immutable baseline,
  targets, observed outcome, comparison, value/ROI, limitations, metadata) with
  print support.

Navigation registration mirrors the simulations workspace:
`frontend/src/lib/authorization/capabilities.ts` adds
`TRANSFORMATION_EXPERIMENT_READ` and `TRANSFORMATION_EXPERIMENT_MANAGE`; the
`/experiments` route, a primary-navigation item (icon `flask` →
`FlaskConical`), and its capability requirement are registered in
`frontend/src/lib/authorization/navigation.ts` /
`frontend/src/components/navigation-icon.tsx`. Route-auth coverage lives in
`navigation.test.ts` (read reveals the destination; manage alone — without
read — does not).

## Client surface (`frontend/src/lib/experiments/experiments.ts`)

All requests go to `/api/backend/service-operations/experiments` (and
`/api/backend/service-queues` for the queue picker). The module mirrors the
backend schemas and:

- composes the strict create body — `organization_id`, `baseline_snapshot`,
  `observed_outcome`, `outcome_comparison`, and `measurement_status` are never
  client-sent (validated by tests);
- validates the same target-metric rules the schema enforces (at least one
  target, rates 0–100, non-negative non-ROI values, ROI unbounded, queue scope
  excluding value/ROI metrics);
- exposes the lifecycle action map exactly per the backend state machine
  (`draft` → capture-baseline/cancel/archive, `ready` → start/cancel/archive,
  `running` → complete/cancel, `completed` → archive, `cancelled` → archive,
  `archived` → read-only);
- formats values and deltas by wire unit only: rates render `35%` (0–100,
  never rescaled), rate differences render `-3.0 pp` (percentage points,
  never `%`), minutes as `-12 min`, USD signed/dollar, counts signed — all
  presentational, no arithmetic;
- builds a deterministic executive summary and narrative from persisted
  comparison cells (e.g. "Autonomous execution was 24% at baseline, the target
  was 40%, and the observed rate was 35%. The observed value was +11
  percentage points from baseline and -5 percentage points from target."); and
- defines the prominent non-causal disclosure, empty/state hints, and
  status/measurement labels (`EXPERIMENT_CAUSALITY_DISCLAIMER`,
  `experimentStatusHint`, `experimentStatusLabel`, …).

## Baseline / Target / Observed / Projection separation

The workspace keeps each layer visually and semantically distinct:

- **Immutable baseline** — `baseline_snapshot`, labeled "Immutable baseline".
- **Target** — `target_metrics` recorded at creation, with any operator-declared
  `expected_direction`.
- **Observed outcome** — `observed_outcome`, labeled "Observed outcome" with the
  helper "Measured change during the experiment window."
- **Source projection** — `source_scenario_snapshot` rendered as "Source
  projection" with the explanation that linking does not apply the scenario to
  production. Only `evaluated` scenarios are selectable in the create form.

## Comparison semantics

`outcome_comparison.metrics` rows render baseline / target / observed /
change-from-baseline / variance-from-target, plus projected and
variance-from-projection columns whenever any row carries a projection.
Direction verbs (`increased`, `decreased`, `unchanged`, `not_applicable`) are
rendered as neutral badges — never as favourable or losing semantics, and
never ranked. Rate deltas are labelled explicitly in percentage points.

## Value / ROI

Value/ROI rows show projected vs observed vs variance, taken only from the
backend comparison (`projected_value`, `observed_value`,
`variance_from_projection`). When a measurement status other than `measured`
applies, the panel explains the backend reason and an unavailable ROI renders
as "—" — never zero, never recomputed.

## Causality safety

The fixed disclosure "Observed improvement within an experiment window does not
establish that the intervention caused the improvement." is prominent on the
library page, above every detail page, and carried into the persisted
limitations when no comparison exists yet. The executive narrative is
synthesis of backend numbers only; unit tests assert that forbidden causal /
verdict / ranking phrasing never appears.

## Print behavior

Reuses the existing `no-print` fixed header + `print-area` main pattern with
`window.print()`; `globals.css` media-print rules remain unchanged.

## Tests

`frontend/src/lib/experiments/experiments.test.ts` (wired into the `test:auth`
script) covers capability gating, create-payload strictness (no tenant /
snapshot / measurement fields), target validation, the lifecycle matrix, the
pp-vs-% formatting contract, the deterministic executive summary / narrative,
value-row sourcing from backend cells, and error propagation (409 details).
`navigation.test.ts` covers the new route authorization rules.

## Files

- `frontend/src/lib/experiments/experiments.ts`
- `frontend/src/lib/experiments/experiments.test.ts`
- `frontend/src/app/(control-center)/experiments/layout.tsx`
- `frontend/src/app/(control-center)/experiments/page.tsx`
- `frontend/src/app/(control-center)/experiments/[experimentId]/page.tsx`
- `frontend/src/components/experiments/status-badge.tsx`
- `frontend/src/lib/authorization/capabilities.ts` (experiment read/manage
  capabilities)
- `frontend/src/lib/authorization/navigation.ts` / `navigation.test.ts`
- `frontend/src/components/navigation-icon.tsx` (`flask`)
- `frontend/package.json` (`test:auth` script)

No backend file was modified in Phase 1O.2.