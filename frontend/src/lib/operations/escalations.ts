/**
 * Pure helpers for the Service Operations escalation UI.
 *
 * These functions are deterministic, dependency-free, and safe to run on both
 * the server and the client. They contain no policy decisions: capability
 * checks are handled by the React components that consume these helpers.
 */

export type EscalationStage =
  | "breached"
  | "due_soon"
  | "open"
  | "acknowledged"
  | "resolved";

export type EscalationSummary = {
  total: number;
  active: number;
  unacknowledged: number;
  due_soon: number;
  breached: number;
};

export type EscalationListItem = {
  id: number;
  ticket_id: number;
  conversation_id: number | null;
  subject: string;
  priority: string;
  status: string;
  service_queue_id: number | null;
  service_queue_name: string | null;
  assigned_subject: string | null;
  milestone: string;
  stage: EscalationStage;
  due_at: string | null;
  triggered_at: string;
  acknowledged_at: string | null;
  acknowledged_by_subject: string | null;
  resolved_at: string | null;
  resolution_reason: string | null;
  resolution_sla_cycle: string | null;
};

export type WorkloadMember = {
  subject: string;
  open_assigned: number;
  breached: number;
  urgent: number;
  needs_response: number;
  due_soon: number;
};

export const ESCALATION_STAGES: readonly EscalationStage[] = [
  "breached",
  "due_soon",
  "open",
  "acknowledged",
  "resolved",
] as const;

export const SLA_EVENT_TYPES: readonly string[] = [
  "sla.first_response.due_soon",
  "sla.first_response.breached",
  "sla.resolution.due_soon",
  "sla.resolution.breached",
] as const;

export const VALID_PRIORITIES: readonly string[] = [
  "low",
  "normal",
  "high",
  "urgent",
] as const;

export type BadgeVariant = "default" | "success" | "warning" | "danger" | "info";

/**
 * Map an escalation stage to a badge variant.
 */
export function escalationStageVariant(stage: string): BadgeVariant {
  switch (stage) {
    case "breached":
      return "danger";
    case "due_soon":
      return "warning";
    case "acknowledged":
      return "info";
    case "resolved":
      return "success";
    case "open":
    default:
      return "default";
  }
}

/**
 * Build the summary-card data shown at the top of the Escalations tab.
 */
export function buildEscalationSummaryCards(summary: EscalationSummary) {
  return [
    { label: "Total", value: summary.total },
    { label: "Active", value: summary.active },
    { label: "Unacknowledged", value: summary.unacknowledged },
    { label: "Due soon", value: summary.due_soon },
    { label: "Breached", value: summary.breached },
  ];
}

/**
 * Format an ISO-8601 timestamp into a concise, human-readable string.
 * Returns "—" when the value is missing or invalid.
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export type AutomationAction = {
  priority?: string;
  service_queue_key?: string;
  category?: string;
};

export type AutomationRuleInput = {
  name: string;
  event_type: string;
  enabled: boolean;
  actions: AutomationAction;
};

export type AutomationRule = AutomationRuleInput & {
  id: number;
};

/**
 * Validate a candidate SLA automation rule before sending it to the backend.
 * Returns an error message when invalid, or null when the rule is acceptable.
 */
export function validateAutomationRule(
  rule: AutomationRuleInput,
): string | null {
  const name = rule.name.trim();
  if (!name) {
    return "Rule name is required.";
  }

  if (!SLA_EVENT_TYPES.includes(rule.event_type)) {
    return "Select a valid SLA escalation event type.";
  }

  const { priority, service_queue_key, category } = rule.actions;

  if (priority !== undefined && priority !== "") {
    if (!VALID_PRIORITIES.includes(priority)) {
      return `Priority must be one of: ${VALID_PRIORITIES.join(", ")}.`;
    }
  }

  if (service_queue_key !== undefined && service_queue_key !== "") {
    if (service_queue_key.trim().length === 0) {
      return "Queue key cannot be empty.";
    }
  }

  if (
    (priority === undefined || priority === "") &&
    (service_queue_key === undefined || service_queue_key === "") &&
    (category === undefined || category === "")
  ) {
    return "At least one action (priority, queue key, or category) is required.";
  }

  return null;
}

/**
 * Build a short, readable summary of an automation rule's actions.
 */
export function automationActionSummary(actions: AutomationAction): string {
  const parts: string[] = [];
  if (actions.priority) parts.push(`priority → ${actions.priority}`);
  if (actions.service_queue_key)
    parts.push(`queue → ${actions.service_queue_key}`);
  if (actions.category) parts.push(`category → ${actions.category}`);
  return parts.length > 0 ? parts.join(", ") : "No actions configured";
}
