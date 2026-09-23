/**
 * Pure helpers for the Dashboard's escalation summary card.
 *
 * Deterministic and dependency-free so the endpoint choice, the
 * EscalationSummary -> {unacknowledged, breached} mapping, and the ticket.read
 * gate can be unit-tested with the Node built-in runner. The gate mirrors the
 * `/service-operations/escalations/summary` (and `/summary`) route's ticket.read
 * requirement for UX only; the backend enforces the authorization.
 */
import { CAPABILITIES, type Capability } from "../authorization/capabilities.ts";
import type { EscalationSummary } from "../operations/escalations.ts";

export const ESCALATION_SUMMARY_ENDPOINT =
  "/api/backend/service-operations/escalations/summary";

export type DashboardEscalationCounts = {
  unacknowledged: number;
  breached: number;
};

/**
 * The dashboard card renders the two headline SLA numbers from the
 * ticket.read-gated service-operations summary — never the observability
 * `/service/escalations` endpoint, which requires observability.read and would
 * 403 for a ticket.read-only user.
 */
export function escalationSummaryToCounts(
  summary: EscalationSummary | null,
): DashboardEscalationCounts {
  return {
    unacknowledged: summary?.unacknowledged ?? 0,
    breached: summary?.breached ?? 0,
  };
}

/**
 * ticket.read gate for the Dashboard's service-operations section (summary +
 * escalation summary). Without it the section is neither fetched nor rendered.
 */
export function canViewServiceOperations(
  can: (capability: Capability) => boolean,
): boolean {
  return can(CAPABILITIES.TICKET_READ);
}