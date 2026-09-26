/**
 * Frontend surface for the Transformation Experiment workspace.
 *
 * The backend Phase 1O.1 engine (`app/services/service_transformation_experiment_engine.py`)
 * is the single authority for experiment measurement, comparison, and value/ROI
 * arithmetic. This module never recomputes a comparison, an ROI, or a verdict:
 * it mirrors the exact response contracts, composes requests through the
 * existing BFF proxy, and renders what the backend returned.
 *
 * Types below mirror the backend Pydantic schemas one-to-one:
 * - `ServiceTransformationExperimentCreate` body
 * - `ServiceTransformationExperimentRead` rows
 * - measurement snapshots (baseline / observed outcome)
 * - `outcome_comparison` persisted by the comparison engine
 *
 * Rate targets travel as 0-100 percentages on the wire — never divided or
 * multiplied by 100. Rate differences are percentage POINTS on the wire and are
 * always presented as "pp", never as "%". No field is ever recomputed here.
 */
import {
  CAPABILITIES,
  type Capability,
} from "../authorization/capabilities.ts";

export const EXPERIMENT_PATH = "/service-operations/experiments";

/** Proxy prefix for all backend calls (the BFF route owns auth/session). */
export const EXPERIMENT_PROXY_PREFIX = "/api/backend";

export const EXPERIMENT_CAUSALITY_DISCLAIMER =
  "Observed improvement within an experiment window does not establish that the intervention caused the improvement.";

export const EXPERIMENT_WINDOW_DAYS: readonly number[] = [7, 30, 90];

export const EXPERIMENT_STATUS_DRAFT = "draft";
export const EXPERIMENT_STATUS_READY = "ready";
export const EXPERIMENT_STATUS_RUNNING = "running";
export const EXPERIMENT_STATUS_COMPLETED = "completed";
export const EXPERIMENT_STATUS_CANCELLED = "cancelled";
export const EXPERIMENT_STATUS_ARCHIVED = "archived";

export const EXPERIMENT_SCOPE_ORGANIZATION = "organization";
export const EXPERIMENT_SCOPE_QUEUE = "queue";

export const EXPERIMENT_MEASUREMENT_STATUS_MEASURED = "measured";
export const EXPERIMENT_MEASUREMENT_STATUS_INSUFFICIENT_SAMPLE =
  "insufficient_sample";
export const EXPERIMENT_MEASUREMENT_STATUS_PRICING_UNAVAILABLE =
  "pricing_unavailable";
export const EXPERIMENT_MEASUREMENT_STATUS_NO_OBSERVED_ACTIVITY =
  "no_observed_activity";
export const EXPERIMENT_MEASUREMENT_STATUS_INCOMPLETE_WINDOW =
  "incomplete_window";
export const EXPERIMENT_MEASUREMENT_STATUS_NOT_MEASURED = "not_measured";

export const EXPERIMENT_LOAD_ERROR = "Could not load transformation experiments.";
export const EXPERIMENT_CREATE_ERROR = "Could not create the experiment.";
export const EXPERIMENT_ACTION_ERROR =
  "The experiment could not be updated. It may have changed state elsewhere.";

export type ExperimentStatus = string;

/**
 * One comparison dimension in `outcome_comparison.metrics` as the engine
 * declares it. `direction_*` is a neutral, backend-provided verb — the UI
 * renders it verbatim and never turns it into a favourable/judgmental label.
 */
export type ExperimentComparisonDirection =
  | "increased"
  | "decreased"
  | "unchanged"
  | "not_applicable";

export interface ExperimentComparisonMetric {
  metric: string;
  baseline_value: number | null;
  target_value: number | null;
  observed_value: number | null;
  change_from_baseline: number | null;
  variance_from_target: number | null;
  projected_value: number | null;
  variance_from_projection: number | null;
  unit: string;
  direction_vs_baseline: ExperimentComparisonDirection;
  direction_vs_target: ExperimentComparisonDirection;
  direction_vs_projection: ExperimentComparisonDirection | null;
  measurement_status: string;
  warning: string | null;
}

export interface ExperimentWindow {
  start: string | null;
  end: string | null;
  days: number;
}

export interface ExperimentOutcomeComparison {
  comparison_version: string;
  measurement_status: string;
  window: ExperimentWindow | null;
  metrics: ExperimentComparisonMetric[];
  warnings: string[];
  limitations: string[];
}

/** Mirrors `SourceScenarioSnapshot` persisted at create time by the backend. */
export interface ExperimentSourceScenarioSnapshot {
  id: number;
  name: string;
  window_days: number;
  status: string;
  formula_version: string | null;
  projected_result: Record<string, unknown> | null;
}

/** Mirrors `ServiceTransformationExperimentHypothesis`. */
export interface ExperimentHypothesis {
  summary: string;
  change_description: string | null;
  expected_direction: Record<string, string> | null;
}

export interface ExperimentValueBlock {
  estimated_minutes_saved: number | null;
  estimated_hours_saved: number | null;
  estimated_labor_savings_usd: number | null;
  agent_ai_cost_usd: number | null;
  estimated_net_savings_usd: number | null;
  pricing_configured: boolean | null;
  measurement_status: string | null;
  minimum_autonomous_samples: number | null;
  sample_size_sufficient: boolean | null;
  roi_percent: number | null;
}

/** Measurement snapshot shared by baseline and observed outcome. */
export interface ExperimentMeasurementSnapshot {
  scope_type: string;
  scope_key: string | null;
  window_days: number;
  observed_at: string;
  current_window_start: string;
  current_window_end: string;
  tickets_resolved: number;
  reopened_tickets: number;
  first_responses: number;
  reopen_events: number;
  first_response_sla_breaches: number;
  resolution_sla_breaches: number;
  total_sla_breaches: number;
  agent_runs: number;
  autonomous_executions: number;
  human_approval_required: number;
  knowledge_specialist_runs: number;
  average_first_response_minutes: number | null;
  average_resolution_time_minutes: number | null;
  rates: {
    autonomous_execution_rate: number | null;
    human_approval_rate: number | null;
    knowledge_usage_rate: number | null;
    reopen_rate: number | null;
  };
  value: ExperimentValueBlock | null;
}

/** Mirrors `ServiceTransformationExperimentRead`. */
export interface ServiceTransformationExperiment {
  id: number;
  organization_id: number;
  name: string;
  description: string | null;
  scope_type: string;
  scope_key: string | null;
  baseline_window_days: number;
  measurement_window_days: number;
  hypothesis: ExperimentHypothesis;
  target_metrics: Record<string, number>;
  source_scenario_id: number | null;
  source_scenario_snapshot: ExperimentSourceScenarioSnapshot | null;
  planned_start_at: string | null;
  planned_end_at: string | null;
  status: string;
  baseline_captured_at: string | null;
  baseline_snapshot: ExperimentMeasurementSnapshot | null;
  actual_started_at: string | null;
  actual_ended_at: string | null;
  completion_summary: string | null;
  measured_at: string | null;
  observed_outcome: ExperimentMeasurementSnapshot | null;
  outcome_comparison: ExperimentOutcomeComparison | null;
  measurement_status: string | null;
  comparison_version: string | null;
  archiving_reason: string | null;
  archived_at: string | null;
  created_by_subject: string | null;
  created_at: string;
  updated_at: string;
}

/** Mirrors `ServiceTransformationExperimentCreate`. */
export interface ServiceTransformationExperimentCreate {
  name: string;
  description: string | null;
  scope_type: string;
  scope_key: string | null;
  baseline_window_days: number;
  measurement_window_days: number;
  planned_start_at: string | null;
  planned_end_at: string | null;
  hypothesis: {
    summary: string;
    change_description: string | null;
    expected_direction: Record<string, string> | null;
  };
  target_metrics: Record<string, number>;
  source_scenario_id: number | null;
}

/** Mirrors `ServiceQueue` returned by the existing service-queues endpoint. */
export interface ServiceQueue {
  id: number;
  key: string;
  name: string;
  active: boolean;
  is_default: boolean;
  sla_policy_id: number | null;
}

export interface ExperimentExperience {
  readonly canRead: boolean;
  readonly canManage: boolean;
  readonly readOnly: boolean;
}

/** Capability gate derived exclusively from backend-provided capabilities. */
export function canViewExperiments(
  can: (capability: Capability) => boolean,
): boolean {
  return can(CAPABILITIES.TRANSFORMATION_EXPERIMENT_READ);
}

export function canManageExperiments(
  can: (capability: Capability) => boolean,
): boolean {
  return can(CAPABILITIES.TRANSFORMATION_EXPERIMENT_MANAGE);
}

/**
 * Read shows list/detail; management controls never render for a read-only
 * subject. The backend remains authoritative for every request.
 */
export function deriveExperimentExperience(
  can: (capability: Capability) => boolean,
): ExperimentExperience {
  const canRead = canViewExperiments(can);
  const canManage = canManageExperiments(can);
  return { canRead, canManage, readOnly: canRead && !canManage };
}

/**
 * Metric kinds declared by the experiment engine. Used only for presentation
 * (which formatter renders a value) — arithmetic stays on the backend.
 */
export type ExperimentMetricKind =
  | "rate"
  | "count"
  | "minutes"
  | "amount"
  | "roi";

export type ExperimentMetricCategory = "ai-adoption" | "service-quality" | "value";

export interface ExperimentMetricDefinition {
  key: string;
  label: string;
  shortLabel: string;
  helper: string;
  category: ExperimentMetricCategory;
  kind: ExperimentMetricKind;
  unit: string;
}

/**
 * Display metadata for every metric the engine can compare. The definition
 * list is the frontend mirror of `_METRIC_SPECS`; it adds no formula.
 */
export const EXPERIMENT_METRIC_DEFINITIONS: readonly ExperimentMetricDefinition[] =
  [
    {
      key: "autonomous_execution_rate",
      label: "Autonomous execution",
      shortLabel: "Autonomous execution",
      helper:
        "Share of agent runs executed without human approval during the window.",
      category: "ai-adoption",
      kind: "rate",
      unit: "percentage_points",
    },
    {
      key: "human_approval_rate",
      label: "Human approval",
      shortLabel: "Human approval",
      helper: "Share of agent runs requiring human approval during the window.",
      category: "ai-adoption",
      kind: "rate",
      unit: "percentage_points",
    },
    {
      key: "knowledge_usage_rate",
      label: "Knowledge usage",
      shortLabel: "Knowledge usage",
      helper:
        "Share of resolved tickets that involved knowledge-specialist assistance.",
      category: "ai-adoption",
      kind: "rate",
      unit: "percentage_points",
    },
    {
      key: "reopen_rate",
      label: "Reopen rate",
      shortLabel: "Reopen rate",
      helper: "Share of resolved tickets reopened within the window.",
      category: "service-quality",
      kind: "rate",
      unit: "percentage_points",
    },
    {
      key: "total_sla_breaches",
      label: "Total SLA breaches",
      shortLabel: "SLA breaches",
      helper: "Total first-response and resolution SLA breaches in the window.",
      category: "service-quality",
      kind: "count",
      unit: "count",
    },
    {
      key: "average_first_response_minutes",
      label: "Average first response",
      shortLabel: "First response",
      helper: "Average time to first response, in minutes, during the window.",
      category: "service-quality",
      kind: "minutes",
      unit: "minutes",
    },
    {
      key: "average_resolution_time_minutes",
      label: "Average resolution time",
      shortLabel: "Resolution time",
      helper: "Average time to resolution, in minutes, during the window.",
      category: "service-quality",
      kind: "minutes",
      unit: "minutes",
    },
    {
      key: "estimated_minutes_saved",
      label: "Estimated minutes saved",
      shortLabel: "Minutes saved",
      helper:
        "Estimated staff minutes saved by autonomous execution in the window.",
      category: "value",
      kind: "minutes",
      unit: "minutes",
    },
    {
      key: "estimated_net_savings_usd",
      label: "Estimated net savings",
      shortLabel: "Net savings",
      helper:
        "Estimated labor savings minus measured AI cost in the window.",
      category: "value",
      kind: "amount",
      unit: "usd",
    },
    {
      key: "roi_percent",
      label: "ROI",
      shortLabel: "ROI",
      helper: "Estimated return on AI cost; can be negative.",
      category: "value",
      kind: "roi",
      unit: "percentage",
    },
  ];

export const EXPERIMENT_RATE_METRICS: readonly string[] = [
  "autonomous_execution_rate",
  "human_approval_rate",
  "knowledge_usage_rate",
  "reopen_rate",
];

export const EXPERIMENT_VALUE_METRICS: readonly string[] = [
  "estimated_minutes_saved",
  "estimated_net_savings_usd",
  "roi_percent",
];

/** ROI is a percentage with no non-negative bound (mirrors the backend). */
export const EXPERIMENT_ROI_METRICS: readonly string[] = ["roi_percent"];

export function experimentMetricDefinition(
  key: string,
): ExperimentMetricDefinition | undefined {
  return EXPERIMENT_METRIC_DEFINITIONS.find((def) => def.key === key);
}

export function experimentMetricsByCategory(
  category: ExperimentMetricCategory,
): readonly ExperimentMetricDefinition[] {
  return EXPERIMENT_METRIC_DEFINITIONS.filter(
    (def) => def.category === category,
  );
}

/**
 * Which value metrics the engine can compare for a scope. Queue-scoped
 * experiments cannot target value/ROI metrics (backed by the schema layer);
 * this mirrors that rule for the form.
 */
export function experimentTargetableMetrics(
  scopeType: string,
): readonly ExperimentMetricDefinition[] {
  if (scopeType === EXPERIMENT_SCOPE_QUEUE) {
    return EXPERIMENT_METRIC_DEFINITIONS.filter(
      (def) => def.category !== "value",
    );
  }
  return EXPERIMENT_METRIC_DEFINITIONS;
}

export function isExperimentRateMetric(key: string): boolean {
  return EXPERIMENT_RATE_METRICS.includes(key);
}

export function isExperimentValueMetric(key: string): boolean {
  return EXPERIMENT_VALUE_METRICS.includes(key);
}

export function isExperimentRoiMetric(key: string): boolean {
  return EXPERIMENT_ROI_METRICS.includes(key);
}

export function isSupportedExperimentWindow(days: number): boolean {
  return EXPERIMENT_WINDOW_DAYS.includes(days);
}

export function isFiniteExperimentValue(
  key: string,
  value: number,
): boolean {
  if (!Number.isFinite(value)) {
    return false;
  }
  if (isExperimentRateMetric(key)) {
    return value >= 0 && value <= 100;
  }
  if (isExperimentRoiMetric(key)) {
    return true;
  }
  return value >= 0;
}

export const EXPERIMENT_BLANK_NAME_ERROR = "Experiment name cannot be blank.";
export const EXPERIMENT_BLANK_HYPOTHESIS_ERROR =
  "Hypothesis summary cannot be blank.";
export const EXPERIMENT_NO_TARGET_ERROR =
  "Select at least one target metric.";
export const EXPERIMENT_TARGET_VALUE_ERROR =
  "Each rate target must be a finite percentage between 0 and 100, and other targets must be finite and non-negative.";
export const EXPERIMENT_SCOPE_VALUE_METRIC_ERROR =
  "Value and ROI targets are not available for queue-scoped experiments.";

export function experimentNameError(name: string): string | null {
  return name.trim().length === 0 ? EXPERIMENT_BLANK_NAME_ERROR : null;
}

export function experimentHypothesisSummaryError(
  summary: string,
): string | null {
  return summary.trim().length === 0
    ? EXPERIMENT_BLANK_HYPOTHESIS_ERROR
    : null;
}

/**
 * Validate a target metric set for a scope. The rule mirrors the backend: at
 * least one target, finite values, rates within 0-100, and no value/ROI metrics
 * for queue scope.
 */
export function experimentTargetMetricsError(
  targetMetrics: Record<string, number>,
  scopeType: string,
): string | null {
  const keys = Object.keys(targetMetrics);
  if (keys.length === 0) {
    return EXPERIMENT_NO_TARGET_ERROR;
  }
  if (scopeType === EXPERIMENT_SCOPE_QUEUE) {
    const blocked = keys.filter((key) => isExperimentValueMetric(key));
    if (blocked.length > 0) {
      return EXPERIMENT_SCOPE_VALUE_METRIC_ERROR;
    }
  }
  const invalid = keys.some(
    (key) => !isFiniteExperimentValue(key, targetMetrics[key]),
  );
  return invalid ? EXPERIMENT_TARGET_VALUE_ERROR : null;
}

export function coreExperimentValidationError(input: {
  name: string;
  summary: string;
  targetMetrics: Record<string, number>;
  scopeType: string;
}): string | null {
  return (
    experimentNameError(input.name) ??
    experimentHypothesisSummaryError(input.summary) ??
    experimentTargetMetricsError(input.targetMetrics, input.scopeType)
  );
}

/** Convert a `datetime-local` input value to an ISO timestamp, or null. */
export function toIsoDateTime(local: string | null | undefined): string | null {
  if (!local) {
    return null;
  }
  const date = new Date(local);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date.toISOString();
}

/**
 * Build the create payload for the backend schema.
 *
 * Only the strict fields the backend schema accepts are included:
 * `organization_id`, `baseline_snapshot`, `observed_outcome`,
 * `outcome_comparison`, and `measurement_status` are never part of a create
 * body. `expected_direction` is omitted when the operator set no direction.
 */
export function buildExperimentCreatePayload(input: {
  name: string;
  description: string | null | undefined;
  scopeType: string;
  scopeKey: string | null;
  baselineWindowDays: number;
  measurementWindowDays: number;
  plannedStartAt: string | null | undefined;
  plannedEndAt: string | null | undefined;
  hypothesisSummary: string;
  changeDescription: string | null | undefined;
  expectedDirection: Record<string, string>;
  targetMetrics: Record<string, number>;
  sourceScenarioId: number | null | undefined;
}): ServiceTransformationExperimentCreate {
  const trimmedDescription = input.description?.trim() ?? "";
  const trimmedChange = input.changeDescription?.trim() ?? "";

  const targetMetrics: Record<string, number> = {};
  for (const [key, value] of Object.entries(input.targetMetrics)) {
    if (Number.isFinite(value) && isFiniteExperimentValue(key, value)) {
      targetMetrics[key] = value;
    }
  }

  const expectedDirection: Record<string, string> = {};
  for (const [key, direction] of Object.entries(input.expectedDirection)) {
    if (
      direction === "increase" ||
      direction === "decrease"
    ) {
      expectedDirection[key] = direction;
    }
  }

  return {
    name: input.name.trim(),
    description: trimmedDescription.length > 0 ? trimmedDescription : null,
    scope_type: input.scopeType,
    scope_key:
      input.scopeType === EXPERIMENT_SCOPE_QUEUE ? input.scopeKey ?? null : null,
    baseline_window_days: isSupportedExperimentWindow(input.baselineWindowDays)
      ? input.baselineWindowDays
      : 30,
    measurement_window_days: isSupportedExperimentWindow(
      input.measurementWindowDays,
    )
      ? input.measurementWindowDays
      : 30,
    planned_start_at: toIsoDateTime(input.plannedStartAt),
    planned_end_at: toIsoDateTime(input.plannedEndAt),
    hypothesis: {
      summary: input.hypothesisSummary.trim(),
      change_description: trimmedChange.length > 0 ? trimmedChange : null,
      expected_direction:
        Object.keys(expectedDirection).length > 0 ? expectedDirection : null,
    },
    target_metrics: targetMetrics,
    source_scenario_id: input.sourceScenarioId ?? null,
  };
}

export function experimentIdPath(experimentId: number): string {
  return `${EXPERIMENT_PATH}/${experimentId}`;
}

export function experimentActionPath(
  experimentId: number,
  action: string,
): string {
  return `${experimentIdPath(experimentId)}/${action}`;
}

export function experimentProxyUrl(backendPath: string): string {
  return `${EXPERIMENT_PROXY_PREFIX}${backendPath}`;
}

/** Error carrying the backend status + detail for non-2xx experiment calls. */
export class ExperimentRequestError extends Error {
  readonly status: number;
  readonly detail: string | null;

  constructor(message: string, status: number, detail: string | null) {
    super(message);
    this.name = "ExperimentRequestError";
    this.status = status;
    this.detail = detail;
  }
}

export interface ExperimentResponseDetails {
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
    throw new ExperimentRequestError(
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

/** GET /api/backend/service-operations/experiments */
export async function fetchExperimentList(
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationExperiment[]> {
  return request<ServiceTransformationExperiment[]>(
    experimentProxyUrl(EXPERIMENT_PATH),
    jsonInit("GET"),
    fetcher,
    EXPERIMENT_LOAD_ERROR,
  );
}

/** GET /api/backend/service-operations/experiments/{id} */
export async function fetchExperiment(
  experimentId: number,
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationExperiment> {
  return request<ServiceTransformationExperiment>(
    experimentProxyUrl(experimentIdPath(experimentId)),
    jsonInit("GET"),
    fetcher,
    EXPERIMENT_LOAD_ERROR,
  );
}

/** POST /api/backend/service-operations/experiments */
export async function createExperiment(
  payload: ServiceTransformationExperimentCreate,
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationExperiment> {
  return request<ServiceTransformationExperiment>(
    experimentProxyUrl(EXPERIMENT_PATH),
    jsonInit("POST", payload),
    fetcher,
    EXPERIMENT_CREATE_ERROR,
  );
}

/** POST /api/backend/service-operations/experiments/{id}/{action} */
export async function runExperimentAction(
  experimentId: number,
  action: string,
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationExperiment> {
  return request<ServiceTransformationExperiment>(
    experimentProxyUrl(experimentActionPath(experimentId, action)),
    jsonInit("POST"),
    fetcher,
    EXPERIMENT_ACTION_ERROR,
  );
}

/** GET /api/backend/service-queues (used for the queue-scope picker only). */
export async function fetchServiceQueues(
  fetcher: typeof fetch = fetch,
): Promise<ServiceQueue[]> {
  return request<ServiceQueue[]>(
    `${EXPERIMENT_PROXY_PREFIX}/service-queues`,
    jsonInit("GET"),
    fetcher,
    "Could not load service queues.",
  );
}

/** Lifecycle transitions the backend state machine permits per status. */
export type ExperimentLifecycleAction =
  | "capture-baseline"
  | "start"
  | "complete"
  | "cancel"
  | "archive";

export const EXPERIMENT_ACTIONS_BY_STATUS: Record<
  string,
  readonly ExperimentLifecycleAction[]
> = {
  draft: ["capture-baseline", "cancel", "archive"],
  ready: ["start", "cancel", "archive"],
  running: ["complete", "cancel"],
  completed: ["archive"],
  cancelled: ["archive"],
  archived: [],
};

export function experimentActions(
  status: string,
): readonly ExperimentLifecycleAction[] {
  return EXPERIMENT_ACTIONS_BY_STATUS[status] ?? [];
}

export const EXPERIMENT_ACTION_LABELS: Record<ExperimentLifecycleAction, string> =
  {
    "capture-baseline": "Capture baseline",
    start: "Start experiment",
    complete: "Complete & measure",
    cancel: "Cancel experiment",
    archive: "Archive experiment",
  };

/** Primary lifecycle buttons (baseline capture, start, complete) vs. secondary. */
export function isExperimentPrimaryAction(
  action: ExperimentLifecycleAction,
): boolean {
  return (
    action === "capture-baseline" ||
    action === "start" ||
    action === "complete"
  );
}

const EXPERIMENT_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  ready: "Ready",
  running: "Running",
  completed: "Completed",
  cancelled: "Cancelled",
  archived: "Archived",
};

export function experimentStatusLabel(status: string): string {
  return EXPERIMENT_STATUS_LABELS[status] ?? status;
}

const EXPERIMENT_MEASUREMENT_STATUS_LABELS: Record<string, string> = {
  measured: "Measured",
  insufficient_sample: "Insufficient sample",
  pricing_unavailable: "Pricing unavailable",
  no_observed_activity: "No observed activity",
  incomplete_window: "Incomplete window",
  not_measured: "Not yet measured",
};

export function experimentMeasurementStatusLabel(value: string): string {
  return EXPERIMENT_MEASUREMENT_STATUS_LABELS[value] ?? value;
}

const EXPERIMENT_STATUS_HINTS: Record<string, string> = {
  draft: "Capture a baseline before starting.",
  ready: "Baseline captured. This experiment is ready to start.",
  running: "Measurement window is in progress.",
  completed: "Observed outcome has been measured.",
  cancelled: "This experiment was cancelled before completion.",
  archived: "This experiment is archived and read-only.",
};

export function experimentStatusHint(status: string): string | null {
  return EXPERIMENT_STATUS_HINTS[status] ?? null;
}

export function experimentScopeLabel(
  scopeType: string,
  scopeKey: string | null,
): string {
  if (scopeType === EXPERIMENT_SCOPE_ORGANIZATION) {
    return "Organization";
  }
  if (scopeType === EXPERIMENT_SCOPE_QUEUE) {
    return scopeKey ? `Queue: ${scopeKey}` : "Queue";
  }
  return scopeType;
}

/** Render a window shorthand ("30d" / "90d"). */
export function formatExperimentWindowDays(days: number): string {
  return `${days}d`;
}

/** Render a plain 0-100 rate ("24%") or "—". Never divided by 100. */
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

/** Render a plain quantity (minutes), or "—". */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
}

/** Render a signed integer delta ("+3", "-1", "0"), or "—". */
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

/** Render a USD amount with thousands separators, or "—". */
export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return `$${value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/**
 * Render a signed percentage-POINT difference ("-3.0 pp", "+11.0 pp"), or "—".
 * Rate differences travel as percentage points from the backend and are never
 * relabelled as percentages.
 */
export function formatPp(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const signed = value > 0 ? `+${value.toFixed(1)}` : `${value.toFixed(1)}`;
  return `${signed} pp`;
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

const EXPERIMENT_METRIC_NOUNS: Record<string, string> = {
  rate: "rate",
  count: "value",
  minutes: "value",
  amount: "value",
  roi: "ROI",
};

function metricNoun(kind: string): string {
  return EXPERIMENT_METRIC_NOUNS[kind] ?? "value";
}

/** Value of a comparison cell by its metric kind (no arithmetic). */
export function formatMetricCell(
  value: number | null | undefined,
  kind: string,
): string {
  switch (kind) {
    case "rate":
    case "roi":
      return formatRate(value);
    case "count":
      return formatCount(value);
    case "minutes":
      return `${formatNumber(value)} min`;
    case "amount":
      return formatMoney(value);
    default:
      return formatNumber(value);
  }
}

/** Delta of a comparison cell by its wire unit (no arithmetic). */
export function formatMetricDelta(
  value: number | null | undefined,
  unit: string,
): string {
  switch (unit) {
    case "percentage_points":
    case "percentage":
      return formatPp(value);
    case "count":
      return formatDelta(value);
    case "minutes":
      return `${formatDelta(value)} min`;
    case "usd":
      return formatMoneyDelta(value);
    default:
      return formatNumber(value);
  }
}

/** Delta rendered as words for the executive narrative ("+11 percentage points"). */
export function formatMetricDeltaWords(
  value: number | null | undefined,
  unit: string,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const compact = Number(value.toFixed(2));
  const signed = compact > 0 ? `+${compact}` : `${compact}`;
  switch (unit) {
    case "percentage_points":
    case "percentage":
      return `${signed} percentage points`;
    case "count":
      return signed;
    case "minutes":
      return `${signed} min`;
    case "usd":
      return compact > 0
        ? `+$${compact}`
        : compact < 0
          ? `-$${Math.abs(compact)}`
          : "$0";
    default:
      return signed;
  }
}

export interface ExperimentComparisonRow {
  metric: string;
  label: string;
  helper: string | null;
  category: ExperimentMetricCategory;
  baseline: string;
  target: string;
  observed: string;
  changeFromBaseline: string;
  varianceFromTarget: string;
  projected: string | null;
  varianceFromProjection: string | null;
  directionVsBaseline: ExperimentComparisonDirection;
  directionVsTarget: ExperimentComparisonDirection;
  measurementStatus: string;
  measurementStatusLabel: string;
}

export interface ExperimentExecutiveValueRow {
  label: string;
  projected: string;
  observed: string;
  variance: string;
}

export interface ExperimentExecutiveSummary {
  name: string;
  scopeLabel: string;
  scopeKeyNote: string | null;
  baselineWindowLabel: string;
  measurementWindowLabel: string;
  statusLabel: string;
  measurementStatusLabel: string | null;
  measuredAtLabel: string;
  createdBy: string | null;
  comparisonVersion: string | null;
  comparisonRows: ExperimentComparisonRow[];
  valueRows: ExperimentExecutiveValueRow[] | null;
  narrative: string[];
  warnings: string[];
  limitations: string[];
}

/**
 * Deterministic executive summary built entirely from the persisted
 * `outcome_comparison`, `baseline_snapshot`, and `observed_outcome`. No LLM, no
 * ranking, no verdict, no recomputation of any metric.
 */
export function buildExperimentExecutiveSummary(
  experiment: ServiceTransformationExperiment,
): ExperimentExecutiveSummary {
  const comparison = experiment.outcome_comparison;

  const rows = (comparison?.metrics ?? []).map((metric) => {
    const definition = experimentMetricDefinition(metric.metric);
    const kind = definition?.kind ?? "count";
    return {
      metric: metric.metric,
      label: definition?.label ?? formatAssumptionLabelLike(metric.metric),
      helper: definition?.helper ?? null,
      category: definition?.category ?? "service-quality",
      baseline: formatMetricCell(metric.baseline_value, kind),
      target: formatMetricCell(metric.target_value, kind),
      observed: formatMetricCell(metric.observed_value, kind),
      changeFromBaseline: formatMetricDelta(
        metric.change_from_baseline,
        metric.unit,
      ),
      varianceFromTarget: formatMetricDelta(
        metric.variance_from_target,
        metric.unit,
      ),
      projected:
        metric.projected_value === null ||
        metric.projected_value === undefined
          ? null
          : formatMetricCell(metric.projected_value, kind),
      varianceFromProjection:
        metric.variance_from_projection === null ||
        metric.variance_from_projection === undefined
          ? null
          : formatMetricDelta(metric.variance_from_projection, metric.unit),
      directionVsBaseline: metric.direction_vs_baseline,
      directionVsTarget: metric.direction_vs_target,
      measurementStatus: metric.measurement_status,
      measurementStatusLabel: experimentMeasurementStatusLabel(
        metric.measurement_status,
      ),
    };
  });

  const valueRows = rows
    .filter(
      (row) =>
        isExperimentValueMetric(row.metric) &&
        row.projected !== null &&
        row.observed !== "—" &&
        row.varianceFromProjection !== null,
    )
    .map((row) => ({
      label: row.label,
      projected: row.projected as string,
      observed: row.observed,
      variance: row.varianceFromProjection as string,
    }));

  const narrative = buildExperimentNarrative(experiment);

  return {
    name: experiment.name,
    scopeLabel: experimentScopeLabel(
      experiment.scope_type,
      experiment.scope_key,
    ),
    scopeKeyNote:
      experiment.scope_type === EXPERIMENT_SCOPE_ORGANIZATION
        ? null
        : experiment.scope_key,
    baselineWindowLabel: formatExperimentWindowDays(
      experiment.baseline_window_days,
    ),
    measurementWindowLabel: formatExperimentWindowDays(
      experiment.measurement_window_days,
    ),
    statusLabel: experimentStatusLabel(experiment.status),
    measurementStatusLabel:
      experiment.measurement_status === null
        ? null
        : experimentMeasurementStatusLabel(experiment.measurement_status),
    measuredAtLabel: formatTimestamp(experiment.measured_at),
    createdBy: experiment.created_by_subject,
    comparisonVersion: experiment.comparison_version,
    comparisonRows: rows,
    valueRows: valueRows && valueRows.length > 0 ? valueRows : null,
    narrative,
    warnings: comparison?.warnings ?? [],
    limitations:
      comparison?.limitations ??
      [EXPERIMENT_CAUSALITY_DISCLAIMER],
  };
}

function formatAssumptionLabelLike(key: string): string {
  return key
    .replaceAll("_", " ")
    .split(" ")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/**
 * Deterministic executive narrative built only from backend-persisted cells.
 *
 * Sentence shape mirrors the documented presentation: "Autonomous execution
 * was 24% at baseline, the target was 40%, and the observed rate was 35%. The
 * observed value was +11 percentage points from baseline and -5 percentage
 * points from target." No causal wording is ever synthesized here.
 */
export function buildExperimentNarrative(
  experiment: ServiceTransformationExperiment,
): string[] {
  const metrics = experiment.outcome_comparison?.metrics ?? [];
  const sentences: string[] = [];

  for (const metric of metrics) {
    const definition = experimentMetricDefinition(metric.metric);
    const kind = definition?.kind ?? "count";
    const label = definition?.label ?? formatAssumptionLabelLike(metric.metric);
    const noun = metricNoun(kind);

    const baseline =
      metric.baseline_value !== null && metric.baseline_value !== undefined;
    const target =
      metric.target_value !== null && metric.target_value !== undefined;
    const observed =
      metric.observed_value !== null && metric.observed_value !== undefined;

    if (!baseline || !target || !observed) {
      continue;
    }

    const firstSentence =
      `${label} was ${formatMetricCell(metric.baseline_value, kind)} at baseline, ` +
      `the target was ${formatMetricCell(metric.target_value, kind)}, and the observed ` +
      `${noun} was ${formatMetricCell(metric.observed_value, kind)}.`;
    sentences.push(firstSentence);

    const changed =
      metric.change_from_baseline !== null &&
      metric.change_from_baseline !== undefined;
    const variance =
      metric.variance_from_target !== null &&
      metric.variance_from_target !== undefined;
    if (changed && variance) {
      sentences.push(
        `The observed value was ${formatMetricDeltaWords(
          metric.change_from_baseline,
          metric.unit,
        )} from baseline and ${formatMetricDeltaWords(
          metric.variance_from_target,
          metric.unit,
        )} from target.`,
      );
    }

    if (
      metric.projected_value !== null &&
      metric.projected_value !== undefined &&
      metric.variance_from_projection !== null &&
      metric.variance_from_projection !== undefined
    ) {
      sentences.push(
        `The scenario projection was ${formatMetricCell(
          metric.projected_value,
          kind,
        )}; the observed value was ${formatMetricDeltaWords(
          metric.variance_from_projection,
          metric.unit,
        )} from the projection.`,
      );
    }
  }

  return sentences;
}