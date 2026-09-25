# Phase 1N.1 — Service Transformation Simulation Engine & Scenario Model

Status: **IMPLEMENTED** (pending final review) · Phase: 1N.1 · Depends on:
Phase 1L Service Transformation analytics, Phase 1D.2 authorization, Phase 1E
tenant context

## Objective

Give operators a generic, tenant-safe **what-if simulation** over the Phase 1L
service transformation readout: persist named *scenarios* with operator-supplied
percentage *assumptions*, capture a read-only snapshot of the observed
transformation *baseline*, and project deterministic *impact* values. Simulation
is deliberately **scenario modelling, not prediction**: it applies the operator's
assumed target rates to the tenant's observed baseline using bounded arithmetic
and transparent formulas, and never fabricates outcomes the baseline could not
support.

> This simulation is deterministic scenario modelling, not a forecast or
> guaranteed business outcome.

### Scope guardrails (applied)

- **Not predictive AI.** No LLM, no ML model, no probabilistic sampling, no
  Monte Carlo, no forecast API. The engine is a pure, side-effect-free function
  with no randomness and no current-time dependence.
- **Observed ≠ assumed ≠ projected.** The API keeps the observed baseline, the
  assumed targets, and the projected values in distinct structures; no field
  mixes them.
- **Operational truth is never mutated.** Simulation never writes Ticket,
  AgentRun, ServiceEscalation, SLA policy, or AIRequestLog rows and never
  rewrites a historical metric. The only writes are the scenario rows themselves.
- **No invented KPIs.** No "transformation score", no employee/customer
  prediction, no CSAT/FCR/sentiment/revenue-lift invention, no second ROI
  formula. Value projections reuse exactly the Phase 1L value-realization
  semantics and only when the observed baseline was itself measurable.
- **Generic.** No automotive-specific simulation concepts or product names.

## Data model

Table `service_transformation_scenarios` (Alembic revision `8520421279e1`,
single head after `1j0a0003`):

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `organization_id` | int FK, **NOT NULL** | tenant boundary; scenario rows never carry NULL-org |
| `name` | varchar(255) | |
| `description` | text nullable | free-form operator note, no PII contract enforced here |
| `window_days` | int | `CHECK IN (7,30,90)` — matches Phase 1L windows |
| `status` | varchar(20) | `CHECK IN ('draft','evaluated','archived')` |
| `assumptions` | JSONB NOT NULL | validated target subset (below) |
| `observed_baseline` | JSONB | bounded snapshot captured at evaluate time |
| `projected_result` | JSONB | deterministic engine output |
| `formula_version` | varchar(20) | `SERVICE_TRANSFORMATION_SIMULATION_VERSION = "1"` |
| `evaluated_at` | timestamptz | when the last snapshot was captured |
| `created_by_subject` | varchar(255) | actor subject, same convention as eval runs |
| `created_at` / `updated_at` | timestamptz | |

Indexes: `(organization_id)`, `(organization_id, created_at)`. No employee-level
performance data or raw customer message text is ever persisted.

## Assumption contract

`SimulationAssumptions` (strict Pydantic model, `extra=forbid`):

- `autonomous_execution_rate_target` — target % of observed agent runs executed autonomously
- `human_approval_rate_target` — target % of observed agent runs requiring human approval
- `knowledge_usage_rate_target` — target % of observed agent runs routed to the knowledge specialist
- `reopen_rate_target` — target reopen % of observed **resolved** tickets (Phase 1L reopen denominator)
- `sla_breach_reduction_percent` — target % reduction of observed total SLA breaches

All five are **0–100 percentages** (never 0–1). Supplied values must be finite
(`NaN`/`±inf` rejected) and in `[0, 100]`; unknown keys and an empty assumption
set are rejected; at least one assumption is required. `None` means the
dimension is not assumed and its projection equals the observed value.

## Baseline capture

`evaluate` re-reads the tenant's current Phase 1L summary via the existing
`ServiceTransformationService.summary_for_tenant(db, organization_id, days)`
for the scenario's `window_days` (7/30/90, default 30). A bounded snapshot is
persisted verbatim as `observed_baseline`, containing the window,
`observed_at`, the flat engine inputs (`agent_runs`, `autonomous_executions`,
`human_approval_required`, `knowledge_specialist_runs`, `reopened_tickets`,
`tickets_resolved`, `total_sla_breaches`), the observed 0–100 rates, and the
value-realization block (including `measurement_status`,
`sample_size_sufficient`, `minimum_autonomous_samples`, `roi_percent`). This
snapshot never replaces or rewrites any Phase 1L table; it is stored only on
the scenario row so the row is self-contained and auditable. No ROI is
projected when the observed baseline is insufficient.

## Deterministic engine

`app/services/service_transformation_simulation_engine.py` — pure module,
no DB, no clock, no randomness. Inputs: observed baseline dict + assumption
subset. Outputs: `projected`, `deltas`, bounded deterministic `warnings`, and
the value `measurement_status`.

### Projection semantics (exact formulas)

1. **Autonomous**: `projected_autonomous = round(agent_runs * target_rate / 100)` clamped to `[0, agent_runs]`. Described as "rate applied to observed agent runs"; eligibility is not modelled.
2. **Human approval**: `projected_approvals = round(agent_runs * target_rate / 100)` clamped to `[0, agent_runs]`. Projected independently — it never becomes a partition of agent outcomes. When both autonomous and approval targets are supplied a warning states they are independent rates and must not be summed.
3. **Knowledge**: `projected_knowledge_runs = round(agent_runs * target_rate / 100)`. Projects usage only, not resolution quality.
4. **Reopen**: `projected_reopened = round(tickets_resolved * target_rate / 100)` using the **same denominator and semantics as the Phase 1L `reopen_rate`** (`reopened_tickets / tickets_resolved`).
5. **SLA**: `projected_breaches = round(total_sla_breaches * (1 - reduction% / 100))`, clamped `>= 0`.

All projected rate fields are reported **0–100**. Every target rate applied to
a zero denominator emits a warning and projects zero rather than an invented
value.

### Value / ROI projection (reuse, not a new formula)

Projected value is the observed instrumented value scaled by the
projected/observed autonomous-execution ratio; projected AI cost is held at
the observed cost because no assumption changes total agent runs. ROI is
recomputed with the **exact Phase 1L formula**
`roi_percent = net_savings / agent_ai_cost * 100`. This output is produced
**only** when the observed baseline is measurable (`pricing_configured` and
`sample_size_sufficient`); otherwise `measurement_status` is inherited
(`pricing_not_configured` | `insufficient_sample`) and ROI is `None` — never
fabricated. No revenue uplift, retention, or profit is ever inferred.

## API

Router `app/api/routes/service_transformation_simulation.py`, prefix
`/service-operations/simulations` (registered in `app/api/router.py`).

| method | path | capability |
| --- | --- | --- |
| GET | `/service-operations/simulations` | `transformation.simulation.read` |
| POST | `/service-operations/simulations` | `transformation.simulation.manage` |
| GET | `/service-operations/simulations/{id}` | `transformation.simulation.read` |
| POST | `/service-operations/simulations/{id}/evaluate` | `transformation.simulation.manage` |
| POST | `/service-operations/simulations/{id}/archive` | `transformation.simulation.manage` |

Create accepts only `name`, `description`, `days`, `assumptions`
(`extra=forbid`); **`organization_id` in the body is rejected**. Tenancy is
resolved exclusively from `CurrentTenant` and enforced in SQL; `evaluate`
re-reads the live observed baseline (no client-supplied baseline). Archived
scenarios reject evaluation with `409`; unknown ids return `404`. The evaluate
response returns `scenario`, `baseline`, `assumptions`, `projected`, `deltas`,
`warnings`, and `measurement_status` as separate structures.

### Tiering

`transformation.simulation.read` / `transformation.simulation.manage` are new
capabilities in `app/core/rbac.py`. Owners and admins hold all capabilities;
supervisors (analysts) hold both simulation capabilities; agents and read-only
viewers are denied. Checks run server-side via `RequireCapability`; the
frontend is out of scope for 1N.1.

## Auditability

- `SERVICE_TRANSFORMATION_SIMULATION_VERSION = "1"` persisted as
  `formula_version` at every evaluation.
- Baseline, assumptions, and projected result are all stored on the row; the
  response renders the same values that were persisted.
- `evaluated_at`, `observed_at`, and the source window make each evaluation
  reproducible and attributable.
- Re-evaluating overwrites the latest snapshot on the same row (auditable,
  simplest single-snapshot design).

## Limitations

- Projections assume observed run counts scale linearly with the target rate;
  they do not model eligibility, queue composition, or execution quality.
- Autonomous and approval targets are projected independently; they are not a
  mutually exclusive partition of agent outcomes.
- Reopened-ticket projections use the Phase 1L reopen denominator semantics and
  do not forecast which tickets reopen.
- Value/ROI is only meaningful when pricing is configured and the observed
  autonomous sample meets the Phase 1L minimum; otherwise the projection
  reports the inherited status and no ROI.
- Every evaluated projection carries warnings; they are the surfaced modelling
  limitations, not qualifiers that can be silenced.

## Tests

`tests/test_service_transformation_simulation.py` covers model/migration
constraints, single-head alembic, strict assumption validation, engine
determinism and formulas, value/ROI states, tenancy isolation, RBAC gates, and
the API lifecycle. The RBAC matrix test in `tests/test_rbac.py` was updated for
the two new supervisor capabilities.

## Out of scope (later phases)

Executive decision workspace and charts, scenario comparison UI, PDF/export,
AI recommendations / LLM explanations, predictive forecasting, Monte Carlo,
staffing optimize, CSAT/FCR/employee/customer prediction, and real financial
forecasting. Recommended follow-up: **Phase 1N.2 — Executive Decision Workspace
& Scenario Comparison**.