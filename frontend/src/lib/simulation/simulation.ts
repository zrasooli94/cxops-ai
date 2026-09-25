/**
 * Frontend surface for the Service Transformation Simulation workspace.
 *
 * The backend Phase 1N.1 engine (`app/services/service_transformation_simulation_engine.py`)
 * is the single authority for scenario arithmetic. This module never recomputes
 * projections or ROI: it mirrors the exact response contracts, composes requests
 * through the existing BFF proxy, and renders what the backend returned.
 *
 * Types below mirror the backend Pydantic schemas one-to-one:
 * - `ServiceTransformationScenarioCreate` body
 * - `ServiceTransformationScenarioRead` scenario rows
 * - `ServiceTransformationScenarioEvaluateResponse` evaluation payload
 *
 * Assumption targets are 0-100 percentages on the wire — never divided or
 * multiplied by 100. A missing assumption is "unchanged / not modelled", never
 * silently zero.
 */
import {
  CAPABILITIES,
  type Capability,
} from "../authorization/capabilities.ts";

export const SIMULATION_PATH = "/service-operations/simulations";

/** Proxy prefix for all backend calls (the BFF route owns auth/session). */
export const SIMULATION_PROXY_PREFIX = "/api/backend";

export const SIMULATION_DISCLAIMER =
  "Scenario projections are deterministic estimates, not forecasts or guaranteed outcomes.";

export const SIMULATION_WINDOW_DAYS: readonly number[] = [7, 30, 90];

export const SIMULATION_DEFAULT_DAYS = 30;

export const SIMULATION_STATUS_DRAFT = "draft";
export const SIMULATION_STATUS_EVALUATED = "evaluated";
export const SIMULATION_STATUS_ARCHIVED = "archived";

export const SIMULATION_LOAD_ERROR =
  "Could not load transformation scenarios.";
export const SIMULATION_CREATE_ERROR =
  "Could not create the scenario.";
export const SIMULATION_EVALUATE_ERROR =
  "Could not evaluate the scenario.";
export const SIMULATION_ARCHIVE_ERROR =
  "Could not archive the scenario.";

export type SimulationStatus =
  | (typeof SIMULATION_STATUS_DRAFT)
  | (typeof SIMULATION_STATUS_EVALUATED)
  | (typeof SIMULATION_STATUS_ARCHIVED);

export type SimulationAssumptionKey =
  | "autonomous_execution_rate_target"
  | "human_approval_rate_target"
  | "knowledge_usage_rate_target"
  | "reopen_rate_target"
  | "sla_breach_reduction_percent";

export const SIMULATION_ASSUMPTION_KEYS: readonly SimulationAssumptionKey[] = [
  "autonomous_execution_rate_target",
  "human_approval_rate_target",
  "knowledge_usage_rate_target",
  "reopen_rate_target",
  "sla_breach_reduction_percent",
];

export const SIMULATION_MAX_ASSUMPTION_VALUE = 100;

export const SIMULATION_ASSUMPTION_SELECTION_ERROR =
  "Select at least one scenario assumption to model.";

export const SIMULATION_ASSUMPTION_VALUE_ERROR =
  "Each assumption must be a finite percentage between 0 and 100.";

export const SIMULATION_BLANK_NAME_ERROR = "Scenario name cannot be blank.";

export const COMPARISON_MIN_SCENARIOS = 2;

export const COMPARISON_MAX_SCENARIOS = 4;

export const COMPARISON_TOO_FEW_MESSAGE =
  "Select at least two evaluated scenarios to compare.";

export const COMPARISON_TOO_MANY_MESSAGE =
  "Select at most four scenarios to compare.";

export const UNCHANGED_NOT_MODELLED =
  "Not assumed — unchanged / not modelled";

export const DIFFERENT_WINDOW_WARNING =
  "These scenarios use different observed windows and are not directly comparable.";

export const DIFFERENT_FORMULA_VERSION_WARNING =
  "These scenarios were evaluated with different formula versions.";

export const COMPARISON_UNEVALUATED_WARNING =
  "One or more scenarios in this comparison have not been evaluated.";

export const ROI_UNAVAILABLE_MESSAGE =
  "Formal ROI is unavailable for this scenario because the measurement requirements were not met.";

export const UNEVALUATED_PROMPT =
  "Evaluate this scenario to capture the current observed baseline and calculate projections.";

export const ARCHIVED_CANNOT_RE_EVALUATE =
  "This scenario is archived and cannot be re-evaluated.";

export const EVALUATE_ACTION_LABEL = "Evaluate";
export const REEVALUATE_ACTION_LABEL = "Re-evaluate";

/**
 * One assumption dimension as the backend `SimulationAssumptions` schema
 * declares it: a finite 0-100 percentage target, or absent.
 */
export type SimulationAssumptionTargets = Partial<
  Record<SimulationAssumptionKey, number>
>;

/** Mirrors `ServiceTransformationScenarioCreate`. */
export interface ServiceTransformationScenarioCreate {
  name: string;
  description: string | null;
  days: number;
  assumptions: SimulationAssumptionTargets;
}

/** Mirrors `SimulationAssumptions` as returned on a scenario row. */
export interface SimulationAssumptionRecord {
  autonomous_execution_rate_target?: number | null;
  human_approval_rate_target?: number | null;
  knowledge_usage_rate_target?: number | null;
  reopen_rate_target?: number | null;
  sla_breach_reduction_percent?: number | null;
}

/** Observed rate block emitted inside the persisted baseline snapshot. */
export interface SimulationObservedRates {
  autonomous_execution_rate: number | null;
  human_approval_rate: number | null;
  knowledge_usage_rate: number | null;
  reopen_rate: number | null;
}

/** Observed value-realization block from the Phase 1L baseline snapshot. */
export interface SimulationObservedValue {
  estimated_minutes_saved: number;
  estimated_hours_saved: number;
  estimated_labor_savings_usd: number;
  agent_ai_cost_usd: number;
  estimated_net_savings_usd: number;
  pricing_configured: boolean;
  measurement_status: string;
  minimum_autonomous_samples: number;
  sample_size_sufficient: boolean;
  roi_percent: number | null;
}

/** The flat observed-baseline snapshot the engine consumes verbatim. */
export interface SimulationBaseline {
  organization_id: number;
  window_days: number;
  observed_at: string;
  current_window_start: string;
  current_window_end: string;
  tickets_resolved: number;
  reopened_tickets: number;
  total_sla_breaches: number;
  agent_runs: number;
  autonomous_executions: number;
  human_approval_required: number;
  knowledge_specialist_runs: number;
  rates: SimulationObservedRates;
  value: SimulationObservedValue;
}

/** Projected value block produced by the deterministic engine. */
export interface SimulationProjectedValue {
  estimated_minutes_saved: number;
  estimated_hours_saved: number;
  estimated_labor_savings_usd: number;
  agent_ai_cost_usd: number;
  estimated_net_savings_usd: number;
  roi_percent: number | null;
  measurement_status: string;
}

/** Projected-result block persisted on an evaluated scenario row. */
export interface SimulationProjectedResult {
  autonomous_executions: number;
  autonomous_execution_rate_percent: number | null;
  human_approval_required: number;
  human_approval_rate_percent: number | null;
  knowledge_specialist_runs: number;
  knowledge_usage_rate_percent: number | null;
  reopened_tickets: number;
  reopen_rate_percent: number | null;
  total_sla_breaches: number;
  sla_breach_reduction_percent: number | null;
  value: SimulationProjectedValue;
}

/** Mirrors `ServiceTransformationScenarioRead`. */
export interface ServiceTransformationScenario {
  id: number;
  organization_id: number;
  name: string;
  description: string | null;
  window_days: number;
  status: SimulationStatus | string;
  assumptions: SimulationAssumptionRecord;
  observed_baseline: SimulationBaseline | null;
  projected_result: SimulationProjectedResult | null;
  formula_version: string | null;
  evaluated_at: string | null;
  created_by_subject: string | null;
  created_at: string;
  updated_at: string;
}

/** Mirrors `ServiceTransformationScenarioEvaluateResponse`. */
export interface ServiceTransformationScenarioEvaluate {
  scenario: ServiceTransformationScenario;
  baseline: SimulationBaseline;
  assumptions: SimulationAssumptionRecord;
  projected: SimulationProjectedResult;
  deltas: Record<string, number>;
  warnings: string[];
  measurement_status: string;
}

export interface SimulationExperience {
  readonly canRead: boolean;
  readonly canManage: boolean;
  readonly readOnly: boolean;
}

/** Capability gate derived exclusively from backend-provided capabilities. */
export function canViewSimulations(
  can: (capability: Capability) => boolean,
): boolean {
  return can(CAPABILITIES.TRANSFORMATION_SIMULATION_READ);
}

export function canManageSimulations(
  can: (capability: Capability) => boolean,
): boolean {
  return can(CAPABILITIES.TRANSFORMATION_SIMULATION_MANAGE);
}

/**
 * Read shows list/detail/compare; management controls never render for a
 * read-only subject. The backend remains authoritative for every request.
 */
export function deriveSimulationExperience(
  can: (capability: Capability) => boolean,
): SimulationExperience {
  const canRead = canViewSimulations(can);
  const canManage = canManageSimulations(can);
  return { canRead, canManage, readOnly: canRead && !canManage };
}

export interface SimulationAssumptionDefinition {
  key: SimulationAssumptionKey;
  label: string;
  shortLabel: string;
  helper: string;
}

/** Display metadata for the five strict assumption dimensions. */
export const SIMULATION_ASSUMPTION_DEFINITIONS: readonly SimulationAssumptionDefinition[] =
  [
    {
      key: "autonomous_execution_rate_target",
      label: "Autonomous execution rate target",
      shortLabel: "Autonomous execution",
      helper:
        "Apply the target rate to observed agent runs. Eligibility is not modelled.",
    },
    {
      key: "human_approval_rate_target",
      label: "Human approval rate target",
      shortLabel: "Human approval",
      helper:
        "Applied to observed agent runs as independent approval demand; it is not a forecast of approval volume.",
    },
    {
      key: "knowledge_usage_rate_target",
      label: "Knowledge usage rate target",
      shortLabel: "Knowledge usage",
      helper:
        "Models specialist usage only; it does not imply better resolution quality.",
    },
    {
      key: "reopen_rate_target",
      label: "Reopen rate target",
      shortLabel: "Reopen rate",
      helper: "Applied to the observed resolved-ticket denominator.",
    },
    {
      key: "sla_breach_reduction_percent",
      label: "SLA breach reduction",
      shortLabel: "SLA breaches",
      helper:
        "Arithmetic reduction of observed breaches; this is not a forecast.",
    },
  ];

export function assumptionDefinition(
  key: SimulationAssumptionKey,
): SimulationAssumptionDefinition | undefined {
  return SIMULATION_ASSUMPTION_DEFINITIONS.find((def) => def.key === key);
}

/** Display label for an assumption key, prettified for unknown keys. */
export function formatAssumptionLabel(key: string): string {
  const def = SIMULATION_ASSUMPTION_DEFINITIONS.find(
    (candidate) => candidate.key === key,
  );
  if (def) return def.label;
  return key
    .replaceAll("_", " ")
    .split(" ")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function isSupportedScenarioWindow(days: number): boolean {
  return SIMULATION_WINDOW_DAYS.includes(days);
}

export function isFiniteAssumptionValue(value: number): boolean {
  return Number.isFinite(value) && value >= 0 && value <= 100;
}

export const isFinitePercentage = isFiniteAssumptionValue;

export function assumptionSelectionError(
  assumptions: SimulationAssumptionTargets,
): string | null {
  if (Object.keys(assumptions).length === 0) {
    return SIMULATION_ASSUMPTION_SELECTION_ERROR;
  }
  return null;
}

export function assumptionValueError(
  value: number | null | undefined,
): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  return isFiniteAssumptionValue(value) ? null : SIMULATION_ASSUMPTION_VALUE_ERROR;
}

export function scenarioNameError(name: string): string | null {
  return name.trim().length === 0 ? SIMULATION_BLANK_NAME_ERROR : null;
}

/**
 * Build the create payload for the backend schema.
 *
 * Only the operator-enabled assumptions are included (a disabled dimension is
 * omitted — the backend treats an absent key as "unchanged / not modelled").
 * Values travel as 0-100 percentages untouched. No unsupported field is ever
 * added (organization_id belongs to the tenant context, never this body).
 */
export function buildScenarioCreatePayload(input: {
  name: string;
  description: string | null | undefined;
  days?: number;
  assumptions: SimulationAssumptionTargets;
}): ServiceTransformationScenarioCreate {
  const assumptions: SimulationAssumptionTargets = {};
  for (const key of SIMULATION_ASSUMPTION_KEYS) {
    const value = input.assumptions[key];
    if (value !== undefined && value !== null && isFiniteAssumptionValue(value)) {
      assumptions[key] = value;
    }
  }

  const trimmedDescription = input.description?.trim() ?? "";
  const days = input.days === undefined ? SIMULATION_DEFAULT_DAYS : input.days;

  return {
    name: input.name.trim(),
    description: trimmedDescription.length > 0 ? trimmedDescription : null,
    days: isSupportedScenarioWindow(days) ? days : SIMULATION_DEFAULT_DAYS,
    assumptions,
  };
}

export function simulationIdPath(scenarioId: number): string {
  return `${SIMULATION_PATH}/${scenarioId}`;
}

export function simulationEvaluatePath(scenarioId: number): string {
  return `${simulationIdPath(scenarioId)}/evaluate`;
}

export function simulationArchivePath(scenarioId: number): string {
  return `${simulationIdPath(scenarioId)}/archive`;
}

export function simulationProxyUrl(backendPath: string): string {
  return `${SIMULATION_PROXY_PREFIX}${backendPath}`;
}

/** Error carrying the backend status + detail for non-2xx simulation calls. */
export class SimulationRequestError extends Error {
  readonly status: number;
  readonly detail: string | null;

  constructor(message: string, status: number, detail: string | null) {
    super(message);
    this.name = "SimulationRequestError";
    this.status = status;
    this.detail = detail;
  }
}

export interface SimulationResponseDetails {
  readonly status: number;
  readonly ok: boolean;
  readonly json: () => Promise<unknown>;
}

async function readDetail(body: unknown): Promise<string | null> {
  if (!body || typeof body !== "object") {
    return null;
  }
  const detail = (body as { detail?: unknown }).detail;
  return typeof detail === "string" ? detail : null;
}

async function request<T>(
  url: string,
  init: RequestInit,
  fetcher: typeof fetch,
  genericLabel: string,
): Promise<T> {
  const response = await fetcher(url, init);
  const body = await response.json().catch(() => null);
  const detail = await readDetail(body);

  if (!response.ok) {
    throw new SimulationRequestError(
      detail ?? genericLabel,
      response.status,
      detail,
    );
  }

  return body as T;
}

function jsonInit(method: string, body?: unknown): RequestInit {
  const init: RequestInit = { method, cache: "no-store" };
  if (body !== undefined) {
    init.headers = { "content-type": "application/json" };
    init.body = JSON.stringify(body);
  }
  return init;
}

/** GET /api/backend/service-operations/simulations */
export async function fetchSimulationList(
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationScenario[]> {
  return request<ServiceTransformationScenario[]>(
    simulationProxyUrl(SIMULATION_PATH),
    jsonInit("GET"),
    fetcher,
    SIMULATION_LOAD_ERROR,
  );
}

/** GET /api/backend/service-operations/simulations/{id} */
export async function fetchSimulation(
  scenarioId: number,
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationScenario> {
  return request<ServiceTransformationScenario>(
    simulationProxyUrl(simulationIdPath(scenarioId)),
    jsonInit("GET"),
    fetcher,
    SIMULATION_LOAD_ERROR,
  );
}

/** POST /api/backend/service-operations/simulations */
export async function createSimulation(
  payload: ServiceTransformationScenarioCreate,
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationScenario> {
  return request<ServiceTransformationScenario>(
    simulationProxyUrl(SIMULATION_PATH),
    jsonInit("POST", payload),
    fetcher,
    SIMULATION_CREATE_ERROR,
  );
}

/** POST /api/backend/service-operations/simulations/{id}/evaluate */
export async function evaluateSimulation(
  scenarioId: number,
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationScenarioEvaluate> {
  return request<ServiceTransformationScenarioEvaluate>(
    simulationProxyUrl(simulationEvaluatePath(scenarioId)),
    jsonInit("POST"),
    fetcher,
    SIMULATION_EVALUATE_ERROR,
  );
}

/** POST /api/backend/service-operations/simulations/{id}/archive */
export async function archiveSimulation(
  scenarioId: number,
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationScenario> {
  return request<ServiceTransformationScenario>(
    simulationProxyUrl(simulationArchivePath(scenarioId)),
    jsonInit("POST"),
    fetcher,
    SIMULATION_ARCHIVE_ERROR,
  );
}

/** Render a backend 0-100 rate, e.g. `40` -> `40%`, or "—". */
export function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const compact = Number(value.toFixed(2)).toString();
  return `${compact}%`;
}

/** Render a count with thousand separators, or "—". */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

/** Render an integer delta with an explicit sign ("+19", "-3", "0"). */
export function formatDelta(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const rounded = Math.round(value);
  if (rounded > 0) return `+${rounded}`;
  return `${rounded}`;
}

/** Render a signed USD delta ("+$45.1", "-$45.1", "$0"), or "—". */
export function formatMoneyDelta(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const compact = Number(value.toFixed(2));
  if (compact > 0) return `+$${compact}`;
  if (compact < 0) return `-$${Math.abs(compact)}`;
  return "$0";
}

/** Render a USD amount with thousands separators, or "—" for null. */
export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return `$${value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Render a scenario window ("7 days", "90 days"). */
export function formatWindowLabel(days: number): string {
  return `${days} days`;
}

/** Render an ISO timestamp compactly, or "—" when absent. */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const SCENARIO_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  evaluated: "Evaluated",
  archived: "Archived",
};

export function formatStatusLabel(status: string): string {
  return SCENARIO_STATUS_LABELS[status] ?? status;
}

const MEASUREMENT_STATUS_LABELS: Record<string, string> = {
  measured: "Measured",
  insufficient_sample: "Insufficient sample",
  pricing_not_configured: "Pricing not configured",
};

export function formatMeasurementStatusLabel(value: string): string {
  return MEASUREMENT_STATUS_LABELS[value] ?? value;
}

/** Assumption summary chip for a library row ("Autonomous 40% · SLA −25%"). */
export function formatAssumptionSummary(
  assumptions: SimulationAssumptionRecord,
): string {
  const parts: string[] = [];
  for (const key of SIMULATION_ASSUMPTION_KEYS) {
    const value = assumptions[key];
    if (value === undefined || value === null) {
      continue;
    }
    const def = assumptionDefinition(key);
    const short = def?.shortLabel ?? formatAssumptionLabel(key);
    if (key === "sla_breach_reduction_percent") {
      parts.push(`${short} −${formatRate(value)}`);
    } else {
      parts.push(`${short} ${formatRate(value)}`);
    }
  }
  return parts.join(" · ");
}

/** True only for a genuinely different observed window across scenarios. */
export function comparisonWindowsDiffer(
  scenarios: readonly ServiceTransformationScenario[],
): boolean {
  const windows = new Set(scenarios.map((scenario) => scenario.window_days));
  return windows.size > 1;
}

/** True only when non-null formula versions differ across scenarios. */
export function comparisonFormulaVersionsDiffer(
  scenarios: readonly ServiceTransformationScenario[],
): boolean {
  const versions = new Set(
    scenarios
      .map((scenario) => scenario.formula_version)
      .filter((version): version is string => version !== null),
  );
  return versions.size > 1;
}

/** True when any selected scenario has never been evaluated. */
export function comparisonHasUnevaluated(
  scenarios: readonly ServiceTransformationScenario[],
): boolean {
  return scenarios.some(
    (scenario) => scenario.status !== SIMULATION_STATUS_EVALUATED,
  );
}

/**
 * Deterministic comparison caveats shown above the matrix.
 *
 * Exact strings declared as constants so the caller renders verbatim; nothing
 * here ranks or recommends a scenario.
 */
export function comparisonWarningsFor(
  scenarios: readonly ServiceTransformationScenario[],
): string[] {
  const warnings: string[] = [];
  if (comparisonWindowsDiffer(scenarios)) {
    warnings.push(DIFFERENT_WINDOW_WARNING);
  }
  if (comparisonFormulaVersionsDiffer(scenarios)) {
    warnings.push(DIFFERENT_FORMULA_VERSION_WARNING);
  }
  if (comparisonHasUnevaluated(scenarios)) {
    warnings.push(COMPARISON_UNEVALUATED_WARNING);
  }
  return warnings;
}

export function comparisonSelectionError(count: number): string | null {
  if (count < COMPARISON_MIN_SCENARIOS) {
    return COMPARISON_TOO_FEW_MESSAGE;
  }
  if (count > COMPARISON_MAX_SCENARIOS) {
    return COMPARISON_TOO_MANY_MESSAGE;
  }
  return null;
}

/**
 * Presentation-only difference between a projected and an observed value.
 *
 * This is the same arithmetic the backend already reports in the evaluate
 * response `deltas`; it is used only to render a delta row on a deep-linked
 * scenario where no evaluate payload is stored. It never feeds any metric.
 */
export function presentationDelta(
  projected: number | null | undefined,
  observed: number | null | undefined,
): number | null {
  if (
    projected === null ||
    projected === undefined ||
    observed === null ||
    observed === undefined
  ) {
    return null;
  }
  return Number((projected - observed).toFixed(2));
}

export interface ExecutiveObservedMetric {
  label: string;
  value: string;
}

/**
 * Tailwind text-color class for a presentation delta, so pages share one
 * mapping instead of repeating nested ternaries.
 */
export function deltaToneClass(delta: number | null | undefined): string {
  if (delta === null || delta === undefined) return "text-slate-300";
  if (delta === 0) return "text-slate-400";
  return delta > 0 ? "text-emerald-600" : "text-rose-500";
}

export interface ExecutiveAssumptionRow {
  label: string;
  value: string;
}

export interface ExecutiveProjectedMetric {
  label: string;
  observed: string;
  projected: string;
  delta: string;
}

export interface ExecutiveValueRow {
  label: string;
  value: string;
}

export interface ExecutiveSummary {
  name: string;
  windowLabel: string;
  statusLabel: string;
  evaluatedAtLabel: string;
  observed: ExecutiveObservedMetric[];
  assumptions: ExecutiveAssumptionRow[];
  projected: ExecutiveProjectedMetric[];
  value: ExecutiveValueRow[] | null;
  roiLabel: string | null;
  measurementStatusLabel: string | null;
  limitations: string[];
}

function executiveObservedMetric(label: string, value: string): ExecutiveObservedMetric {
  return { label, value };
}

/**
 * Deterministic executive summary built entirely from the persisted scenario
 * row + measurement status. No LLM, no ranking, no recommendations.
 */
export function buildExecutiveSummary(scenario: ServiceTransformationScenario): ExecutiveSummary {
  const baseline = scenario.observed_baseline;
  const projected = scenario.projected_result;
  const assumptions = scenario.assumptions;
  const evaluated = baseline !== null && projected !== null;

  const measurementStatus = projected?.value.measurement_status ?? null;
  const measured =
    measurementStatus === "measured" &&
    projected?.value.roi_percent !== null &&
    projected?.value.roi_percent !== undefined;

  const observed: ExecutiveObservedMetric[] = [];
  const projectedMetrics: ExecutiveProjectedMetric[] = [];

  if (evaluated && baseline && projected) {
    const rates = baseline.rates;
    observed.push(
      executiveObservedMetric(
        "Autonomous execution",
        formatRate(rates.autonomous_execution_rate),
      ),
      executiveObservedMetric(
        "Autonomous executions",
        formatCount(baseline.autonomous_executions),
      ),
      executiveObservedMetric(
        "Human approval",
        formatRate(rates.human_approval_rate),
      ),
      executiveObservedMetric(
        "Human approval demand",
        formatCount(baseline.human_approval_required),
      ),
      executiveObservedMetric(
        "Knowledge usage",
        formatRate(rates.knowledge_usage_rate),
      ),
      executiveObservedMetric(
        "Resolved tickets",
        formatCount(baseline.tickets_resolved),
      ),
      executiveObservedMetric("Reopened tickets", formatCount(baseline.reopened_tickets)),
      executiveObservedMetric("Reopen rate", formatRate(rates.reopen_rate)),
      executiveObservedMetric("SLA breaches", formatCount(baseline.total_sla_breaches)),
    );

    projectedMetrics.push(
      executiveProjectedMetricRow(
        "Autonomous executions",
        formatCount(baseline.autonomous_executions),
        formatCount(projected.autonomous_executions),
        presentationDelta(projected.autonomous_executions, baseline.autonomous_executions),
      ),
      executiveProjectedMetricRow(
        "Human approvals",
        formatCount(baseline.human_approval_required),
        formatCount(projected.human_approval_required),
        presentationDelta(projected.human_approval_required, baseline.human_approval_required),
      ),
      executiveProjectedMetricRow(
        "Knowledge specialist runs",
        formatCount(baseline.knowledge_specialist_runs),
        formatCount(projected.knowledge_specialist_runs),
        presentationDelta(
          projected.knowledge_specialist_runs,
          baseline.knowledge_specialist_runs,
        ),
      ),
      executiveProjectedMetricRow(
        "Reopened tickets",
        formatCount(baseline.reopened_tickets),
        formatCount(projected.reopened_tickets),
        presentationDelta(projected.reopened_tickets, baseline.reopened_tickets),
      ),
      executiveProjectedMetricRow(
        "SLA breaches",
        formatCount(baseline.total_sla_breaches),
        formatCount(projected.total_sla_breaches),
        presentationDelta(projected.total_sla_breaches, baseline.total_sla_breaches),
      ),
    );
  }

  const assumptionRows: ExecutiveAssumptionRow[] = [];
  for (const key of SIMULATION_ASSUMPTION_KEYS) {
    const value = assumptions[key];
    if (value === null || value === undefined) {
      continue;
    }
    assumptionRows.push({
      label: formatAssumptionLabel(key),
      value: formatRate(value),
    });
  }

  const valueRows: ExecutiveValueRow[] | null = evaluated
    ? [
        { label: "Estimated minutes saved (projected)", value: formatMoney(projected.value.estimated_minutes_saved) },
        { label: "Estimated hours saved (projected)", value: formatMoney(projected.value.estimated_hours_saved) },
        { label: "Estimated labor savings (projected)", value: formatMoney(projected.value.estimated_labor_savings_usd) },
        { label: "Projected AI cost", value: formatMoney(projected.value.agent_ai_cost_usd) },
        { label: "Estimated net savings (projected)", value: formatMoney(projected.value.estimated_net_savings_usd) },
      ]
    : null;

  const limitations = executiveLimitationsFor(scenario);

  return {
    name: scenario.name,
    windowLabel: formatWindowLabel(scenario.window_days),
    statusLabel: formatStatusLabel(scenario.status),
    evaluatedAtLabel: formatTimestamp(scenario.evaluated_at),
    observed,
    assumptions: assumptionRows,
    projected: projectedMetrics,
    value: valueRows,
    roiLabel: measured ? formatRate(projected?.value.roi_percent ?? null) : null,
    measurementStatusLabel: measurementStatus ? formatMeasurementStatusLabel(measurementStatus) : null,
    limitations,
  };
}

function executiveProjectedMetricRow(
  label: string,
  observed: string,
  projected: string,
  delta: number | null,
): ExecutiveProjectedMetric {
  return { label, observed, projected, delta: formatDelta(delta) };
}

/**
 * Deterministic limitation bullets mirroring the engine's documented modelling
 * assumptions. These restate the engine's own constraints — they never invent
 * a limitation or a stronger claim than the backend makes.
 */
export function executiveLimitationsFor(
  scenario: ServiceTransformationScenario,
): string[] {
  const limitations: string[] = [
    "Deterministic scenario arithmetic applied to the observed baseline; projections are estimates, not forecasts or guaranteed outcomes.",
  ];

  const assumed = new Set(Object.keys(scenario.assumptions));

  if (assumed.has("autonomous_execution_rate_target")) {
    limitations.push("Eligibility of agent runs is not modelled.");
  }
  if (assumed.has("human_approval_rate_target")) {
    limitations.push(
      "Approval demand is projected as an independent rate and does not partition agent outcomes.",
    );
  }
  if (assumed.has("knowledge_usage_rate_target")) {
    limitations.push(
      "Knowledge usage projects specialist usage, not resolution quality.",
    );
  }
  if (assumed.has("reopen_rate_target")) {
    limitations.push(
      "The reopen projection applies its target to the observed resolved-ticket denominator.",
    );
  }
  if (assumed.has("sla_breach_reduction_percent")) {
    limitations.push(
      "SLA reduction is arithmetic against observed breaches; no process or policy change is modelled.",
    );
  }

  const projected = scenario.projected_result;
  const valueStatus = projected?.value.measurement_status;
  if (valueStatus === "insufficient_sample") {
    limitations.push(
      "The observed baseline does not meet the minimum autonomous execution sample, so ROI is unavailable.",
    );
  } else if (valueStatus === "pricing_not_configured") {
    limitations.push("LLM pricing is not configured, so ROI is unavailable.");
  }

  return limitations;
}