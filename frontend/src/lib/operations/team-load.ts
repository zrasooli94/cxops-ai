/**
 * Pure helpers for the Service Operations Team Load tab.
 *
 * These are deterministic and dependency-free so the dual-capability gate, the
 * workload endpoint, and the six-field response contract can be unit-tested
 * with the Node built-in runner. Capability decisions themselves are made by
 * the backend; the gate here mirrors the workload route's requirements
 * (ticket.read AND member.read) for UX only.
 */
import { CAPABILITIES, type Capability } from "../authorization/capabilities.ts";
import type { WorkloadMember } from "./escalations.ts";

export const WORKLOAD_ENDPOINT = "/api/backend/service-operations/workload";

export const WORKLOAD_COLUMNS: readonly (keyof WorkloadMember)[] = [
  "subject",
  "open_assigned",
  "needs_response",
  "due_soon",
  "breached",
  "urgent",
];

/**
 * Dual-capability gate for the Team Load tab.
 *
 * The workload endpoint requires both `ticket.read` and `member.read`, so the
 * tab (and the data fetch mounted with it) must be hidden unless both are
 * present. No role-name check lives here.
 */
export function canViewTeamLoad(
  can: (capability: Capability) => boolean,
): boolean {
  return can(CAPABILITIES.TICKET_READ) && can(CAPABILITIES.MEMBER_READ);
}