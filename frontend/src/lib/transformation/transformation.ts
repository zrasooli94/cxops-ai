/**
 * Read-only frontend helpers for the Service Transformation control-center
 * page.
 *
 * Only the safe time-window GET endpoint is represented here
 * (`/service-operations/transformation?days=7|30|90`); no mutation call exists.
 * The read is gated exactly like the route's capability requirements — the
 * backend requires `ticket.read` and stays authoritative. The types below
 * mirror the backend `ServiceTransformationSummary` contract; a key the
 * backend does not serialize is simply never rendered.
 *
 * Rate fields (ai_analysis_rate, autonomous_execution_rate, escalation_rate,
 * reopen_rate, ...) are emitted by the backend on a 0–100 percentage scale —
 * unlike the observability service which sends 0–1 ratios — so the helpers
 * here format them directly without any multiplication.
 */
import { CAPABILITIES, type Capability } from "../authorization/capabilities.ts";

export const SERVICE_TRANSFORMATION_PATH =
  "/api/backend/service-operations/transformation";

/** Mirrors `ServiceTransformationWindow`. */
export interface ServiceTransformationWindow {
  days: number;
  current_start: string;
  current_end: string;
  previous_start: string;
  previous_end: string;
}

/** Mirrors `ServiceTransformationComparison`. */
export interface ServiceTransformationComparison {
  current: number | null;
  previous: number | null;
  absolute_change: number | null;
  percent_change: number | null;
}

/** Mirrors `ServiceTransformationVolume`. */
export interface ServiceTransformationVolume {
  tickets_created: number;
  tickets_resolved: number;
  currently_open: number;
  currently_needs_response: number;
}

/** Mirrors `ServiceTransformationPerformance`. */
export interface ServiceTransformationPerformance {
  average_first_response_minutes: number | null;
  median_first_response_minutes: number | null;
  average_resolution_time_minutes: number | null;
  median_resolution_time_minutes: number | null;
}

/** Mirrors `ServiceTransformationSLA`. */
export interface ServiceTransformationSLA {
  first_response_sla_breaches: number;
  resolution_sla_breaches: number;
  total_sla_breaches: number;
  due_soon: number;
  escalation_count: number;
  escalation_rate: number | null;
  reopened_tickets: number;
  reopen_rate: number | null;
}

/** Mirrors `ServiceTransformationAIAdoption`. */
export interface ServiceTransformationAIAdoption {
  tickets_analyzed_by_ai: number;
  agent_runs: number;
  autonomous_executions: number;
  human_approval_required: number;
  human_approved: number;
  human_rejected: number;
  successful_agent_executions: number;
  failed_agent_executions: number;
  no_action_runs: number;
  ai_analysis_rate: number | null;
  autonomous_execution_rate: number | null;
  human_approval_rate: number | null;
  execution_success_rate: number | null;
}

/** Mirrors `ServiceTransformationSpecialistUsage`. */
export interface ServiceTransformationSpecialistUsage {
  coordinator_runs: number;
  knowledge_specialist_runs: number;
  action_specialist_runs: number;
  knowledge_usage_rate: number | null;
  pure_action_route_rate: number | null;
  invalid_specialist_path_count: number;
}

/** Mirrors `ServiceTransformationHumanWorkload`. */
export interface ServiceTransformationHumanWorkload {
  human_messages_sent: number;
  ai_executed_replies: number;
}

/** Mirrors `ServiceTransformationValueRealization`. */
export interface ServiceTransformationValueRealization {
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

/** Mirrors `ServiceTransformationChannelBreakdownItem`. */
export interface ServiceTransformationChannelBreakdownItem {
  channel: string;
  conversation_count: number;
  message_count: number;
  percentage: number;
}

/** Mirrors `ServiceTransformationQueueBreakdownItem`. */
export interface ServiceTransformationQueueBreakdownItem {
  queue_key: string | null;
  queue_name: string | null;
  open_tickets: number;
  needs_response: number;
  due_soon: number;
  breached: number;
  priority_urgent_high: number;
  assigned_tickets: number;
  resolved_in_window: number;
  agent_runs: number;
  autonomous_executions: number;
}

/** Mirrors `ServiceTransformationOpportunitySignal`. */
export interface ServiceTransformationOpportunitySignal {
  signal: string;
  scope_type: string;
  scope_key: string;
  evidence: Record<string, number>;
  suggested_focus: string;
}

/** Mirrors `ServiceTransformationSummary`. */
export interface ServiceTransformationSummary {
  generated_at: string;
  window: ServiceTransformationWindow;
  service_volume: ServiceTransformationVolume;
  service_performance: ServiceTransformationPerformance;
  sla: ServiceTransformationSLA;
  ai_adoption: ServiceTransformationAIAdoption;
  specialist_usage: ServiceTransformationSpecialistUsage;
  human_workload: ServiceTransformationHumanWorkload;
  value_realization: ServiceTransformationValueRealization;
  queue_breakdown: ServiceTransformationQueueBreakdownItem[];
  channel_breakdown: ServiceTransformationChannelBreakdownItem[];
  comparisons: Record<string, ServiceTransformationComparison>;
  opportunity_signals: ServiceTransformationOpportunitySignal[];
}

export const TRANSFORMATION_WINDOW_DAYS: readonly number[] = [7, 30, 90];

export const TRANSFORMATION_DEFAULT_DAYS = 30;

/** Safe error label shown when the transformation data cannot be loaded. */
export const SERVICE_TRANSFORMATION_LOAD_ERROR =
  "Could not load transformation data.";

/**
 * Whether the days window is one the backend accepts. Values beyond the
 * bounded 7/30/90 set would be rejected with a 422, so the switcher never
 * sends them.
 */
export function isSupportedTransformationWindow(days: number): boolean {
  return TRANSFORMATION_WINDOW_DAYS.includes(days);
}

/**
 * The windowed GET path. Only backend-supported windows are ever requested;
 * an unsupported value falls back to the default so an unexpected caller never
 * emits a rejecting request.
 */
export function transformationPath(days: number): string {
  const value = isSupportedTransformationWindow(days)
    ? days
    : TRANSFORMATION_DEFAULT_DAYS;
  return `${SERVICE_TRANSFORMATION_PATH}?days=${value}`;
}

/** GET /api/backend/service-operations/transformation?days=30 */
export async function fetchServiceTransformation(
  days: number,
  fetcher: typeof fetch = fetch,
): Promise<ServiceTransformationSummary> {
  const response = await fetcher(transformationPath(days), {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(SERVICE_TRANSFORMATION_LOAD_ERROR);
  }
  return (await response.json()) as ServiceTransformationSummary;
}

/**
 * The capability gate for the whole page. The route requires `ticket.read`
 * (like the shared service-operations data) and the sidebar/render use the
 * same predicate. No role-name check lives here.
 */
export function canViewTransformation(
  can: (capability: Capability) => boolean,
): boolean {
  return can(CAPABILITIES.TICKET_READ);
}

/**
 * Render a backend 0–100 rate as a percentage, e.g. `40` → `40%`,
 * `42.5` → `42.5%`, or "—" when the backend withheld the rate.
 */
export function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  const compact = Number(value.toFixed(2)).toString();
  return `${compact}%`;
}

/** Render a valid comparison delta as an explicitly signed change percent. */
export function formatPercentChange(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  const compact = Number(value.toFixed(2)).toString();
  if (value > 0) {
    return `+${compact}%`;
  }
  return `${compact}%`;
}

/** Render a USD amount with thousands separators. */
export function formatMoney(value: number): string {
  return `$${value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Render a duration in minutes compactly, or "—" when the backend withheld it. */
export function formatMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${Number(value.toFixed(0))} min`;
}

/** Display label for one comparison key path, e.g. "Service Volume · Tickets Created". */
export function comparisonKeyLabel(key: string): string {
  return key
    .split(".")
    .map(formatMetricKey)
    .join(" · ");
}

function formatMetricKey(value: string): string {
  return value
    .replaceAll("_", " ")
    .split(" ")
    .map((part) => {
      if (part === "ai") return "AI";
      if (part === "sla") return "SLA";
      if (part === "roi") return "ROI";
      if (part === "usd") return "USD";
      return part.charAt(0).toUpperCase() + part.slice(1);
    })
    .join(" ");
}

const SIGNAL_LABELS: Record<string, string> = {
  sla_pressure: "SLA pressure",
  ai_adoption: "AI adoption",
  knowledge_utilization: "Knowledge utilization",
  approval_backlog: "Approval backlog",
  reopen_risk: "Reopen risk",
};

/** Render the backend signal literal verbatim, mapped to a display label. */
export function signalLabel(signal: string): string {
  return SIGNAL_LABELS[signal] ?? formatMetricKey(signal);
}

/** Render a scope literal: "Queue" / "Organization", verbatim otherwise. */
export function scopeLabel(scopeType: string): string {
  if (scopeType === "queue") return "Queue";
  if (scopeType === "organization") return "Organization";
  return scopeType;
}