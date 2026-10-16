import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  parseStagingCustomers,
  parseStagingOrganization,
  parseStagingQueue,
  parseStagingTickets,
  stagingTicketMetrics,
} from "./operations-contract.ts";

const ticket = {
  id: 11, organization_id: 1, customer_id: 3,
  external_id: "PHASE1X-ALPHA-T01", subject: "Synthetic: appointment reschedule",
  status: "open", priority: "urgent", source: "synthetic_staging",
  created_at: "2026-10-16T03:00:00Z",
};
const customer = {
  id: 3, organization_id: 1, external_id: "PHASE1X-ALPHA-C01",
  name: "Synthetic Mira Chen", email: "mira.alpha@example.test",
};
const queue = {
  tenant_id: 1,
  queue: { human_requested: 2, human_assigned: 1, active_total: 3 },
  queue_health: { health: "attention", oldest_waiting_minutes: 19 },
};

describe("Phase 1X isolated read-only dashboard contracts", () => {
  it("uses only the single authenticated synthetic workshop", () => {
    assert.deepEqual(parseStagingOrganization([{ id: 1, name: "Synthetic Alpha Workshop" }]),
      { id: 1, name: "Synthetic Alpha Workshop" });
    assert.throws(() => parseStagingOrganization([]));
    assert.throws(() => parseStagingOrganization([
      { id: 1, name: "Synthetic Alpha Workshop" },
      { id: 2, name: "Synthetic Beta Workshop" },
    ]));
    assert.throws(() => parseStagingOrganization([{ id: 1, name: "Customer Production" }]));
  });

  it("rejects cross-tenant tickets, spoofed source and anything not clearly synthetic", () => {
    assert.equal(parseStagingTickets([ticket], 1).length, 1);
    for (const changes of [
      { organization_id: 2 }, { external_id: "PROD-TICKET-1" },
      { subject: "A real customer subject" }, { source: "zendesk" },
      { customer_id: -1 }, { priority: "admin" },
    ]) {
      assert.throws(() => parseStagingTickets([{ ...ticket, ...changes }], 1));
    }
  });

  it("never converts failed or non-synthetic customer data to a zero count", () => {
    assert.equal(parseStagingCustomers({ items: [customer], total: 1 }, 1).length, 1);
    assert.throws(() => parseStagingCustomers({ items: [customer] }, 1));
    assert.throws(() => parseStagingCustomers({ items: [{ ...customer, organization_id: 2 }], total: 1 }, 1));
    assert.throws(() => parseStagingCustomers({ items: [{ ...customer, email: "real@customer.com" }], total: 1 }, 1));
  });

  it("accepts only a tenant-matching aggregate handoff response", () => {
    assert.deepEqual(parseStagingQueue(queue, 1), {
      waiting: 2, assigned: 1, active: 3, oldestWaitingMinutes: 19, attention: true,
    });
    assert.throws(() => parseStagingQueue({ ...queue, tenant_id: 2 }, 1));
    assert.throws(() => parseStagingQueue({
      ...queue, queue: { ...queue.queue, human_requested: -1 },
    }, 1));
    assert.deepEqual(parseStagingQueue({
      ...queue,
      queue: { human_requested: 0, human_assigned: 0, active_total: 0 },
      queue_health: { health: "normal", oldest_waiting_minutes: null },
    }, 1), {
      waiting: 0, assigned: 0, active: 0, oldestWaitingMinutes: null, attention: false,
    });
  });

  it("reports ticket counts from a verified synthetic API payload", () => {
    const items = parseStagingTickets([
      ticket, { ...ticket, id: 12, external_id: "PHASE1X-ALPHA-T02", priority: "high", status: "pending" },
      { ...ticket, id: 13, external_id: "PHASE1X-ALPHA-T03", priority: "normal", status: "solved" },
    ], 1);
    assert.deepEqual(stagingTicketMetrics(items), { total: 3, active: 2, urgent: 1, resolved: 1 });
  });
});
