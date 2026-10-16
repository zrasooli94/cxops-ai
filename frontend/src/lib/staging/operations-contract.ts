/** Strict parsing for the isolated, read-only CXOps staging workspace.
 * Never render arbitrary customer records from a misrouted/non-staging API.
 * This module is pure and independently testable without credentials/network.
 */
export type StagingOrganization = { id: number; name: string };
export type StagingTicket = {
  id: number; organization_id: number; customer_id: number | null;
  external_id: string; subject: string; status: string; priority: string;
  source: "synthetic_staging"; created_at: string;
  assigned_subject: string | null; resolved_at: string | null;
};
export type StagingCustomer = {
  id: number; organization_id: number; external_id: string;
  name: string; email: string;
};
export type StagingQueue = {
  waiting: number; assigned: number; active: number;
  oldestWaitingMinutes: number | null; attention: boolean;
};

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Unexpected staging response");
  }
  return value as Record<string, unknown>;
}

function positiveId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function text(value: unknown, max = 255): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max;
}

function syntheticKey(value: unknown): value is string {
  return text(value, 100) && value.startsWith("PHASE1X-");
}

function boundedCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) &&
    value >= 0 && value <= 1000000;
}

/** A synthetic viewer must have exactly one server-authorized membership. */
export function parseStagingOrganization(value: unknown): StagingOrganization {
  if (!Array.isArray(value) || value.length !== 1) {
    throw new Error("Staging membership must resolve to one workshop");
  }
  const org = record(value[0]);
  if (!positiveId(org.id) || !text(org.name) || !org.name.startsWith("Synthetic ")) {
    throw new Error("Unverified staging organization");
  }
  return { id: org.id, name: org.name };
}

export function parseStagingTickets(value: unknown, organizationId: number): StagingTicket[] {
  if (!Array.isArray(value) || value.length > 50) {
    throw new Error("Untrusted staging tickets");
  }
  return value.map((raw): StagingTicket => {
    const ticket = record(raw);
    if (
      !positiveId(ticket.id) || ticket.organization_id !== organizationId ||
      !syntheticKey(ticket.external_id) ||
      !text(ticket.subject) || !ticket.subject.startsWith("Synthetic: ") ||
      ticket.source !== "synthetic_staging" ||
      !["new", "open", "pending", "solved", "closed"].includes(String(ticket.status)) ||
      !["low", "normal", "high", "urgent"].includes(String(ticket.priority)) ||
      (ticket.customer_id !== null && !positiveId(ticket.customer_id)) ||
      !text(ticket.created_at, 64) ||
      (ticket.assigned_subject !== null && !text(ticket.assigned_subject, 255)) ||
      (ticket.resolved_at !== null && !text(ticket.resolved_at, 64))
    ) {
      throw new Error("A non-synthetic or foreign-tenant ticket was blocked");
    }
    return {
      id: ticket.id, organization_id: organizationId,
      customer_id: ticket.customer_id as number | null,
      external_id: ticket.external_id, subject: ticket.subject,
      status: ticket.status as string, priority: ticket.priority as string,
      source: "synthetic_staging", created_at: ticket.created_at,
      assigned_subject: ticket.assigned_subject as string | null,
      resolved_at: ticket.resolved_at as string | null,
    };
  });
}

export function parseStagingCustomers(value: unknown, organizationId: number): StagingCustomer[] {
  const response = record(value);
  if (!Array.isArray(response.items) || response.items.length > 50 ||
      !boundedCount(response.total)) {
    throw new Error("Untrusted staging customer list");
  }
  return response.items.map((raw): StagingCustomer => {
    const person = record(raw);
    if (
      !positiveId(person.id) || person.organization_id !== organizationId ||
      !syntheticKey(person.external_id) ||
      !text(person.name) || !person.name.startsWith("Synthetic ") ||
      !text(person.email, 255) || !person.email.endsWith("@example.test")
    ) {
      throw new Error("A non-synthetic or foreign-tenant customer was blocked");
    }
    return { id: person.id, organization_id: organizationId, external_id: person.external_id,
      name: person.name, email: person.email };
  });
}

export function parseStagingQueue(value: unknown, organizationId: number): StagingQueue {
  const result = record(value);
  const queue = record(result.queue);
  const health = record(result.queue_health);
  if (
    result.tenant_id !== organizationId ||
    !boundedCount(queue.human_requested) ||
    !boundedCount(queue.human_assigned) ||
    !boundedCount(queue.active_total) ||
    !["normal", "attention"].includes(String(health.health)) ||
    (health.oldest_waiting_minutes !== null &&
      !boundedCount(health.oldest_waiting_minutes))
  ) {
    throw new Error("Untrusted staging handoff summary");
  }
  return {
    waiting: queue.human_requested,
    assigned: queue.human_assigned,
    active: queue.active_total,
    oldestWaitingMinutes: health.oldest_waiting_minutes as number | null,
    attention: health.health === "attention",
  };
}

export function stagingTicketMetrics(tickets: readonly StagingTicket[]) {
  return {
    total: tickets.length,
    active: tickets.filter(t => ["new", "open", "pending"].includes(t.status)).length,
    urgent: tickets.filter(t => t.priority === "urgent" && t.status !== "solved" && t.status !== "closed").length,
    resolved: tickets.filter(t => t.status === "solved" || t.status === "closed").length,
  };
}
