import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { CAPABILITIES } from "../authorization/capabilities.ts";
import { createAuthorizationView } from "../authorization/helpers.ts";
import type { EscalationSummary } from "../operations/escalations.ts";
import {
  canViewServiceOperations,
  escalationSummaryToCounts,
  ESCALATION_SUMMARY_ENDPOINT,
} from "./escalation-summary.ts";

function viewWith(capabilities: readonly string[]) {
  return createAuthorizationView({
    organization_id: 1,
    role: "viewer",
    capabilities,
  });
}

describe("dashboard escalation summary", () => {
  it("1: the card reads the ticket.read service-operations endpoint", () => {
    assert.equal(
      ESCALATION_SUMMARY_ENDPOINT,
      "/api/backend/service-operations/escalations/summary",
    );
  });

  it("2: the card never fetches the observability escalation endpoint", () => {
    assert.ok(
      !ESCALATION_SUMMARY_ENDPOINT.startsWith("/api/backend/observability"),
      "must not depend on the observability.read escalation endpoint",
    );
  });

  it("3: ticket.read shows the service-operations section", () => {
    assert.equal(
      canViewServiceOperations(viewWith([CAPABILITIES.TICKET_READ]).can),
      true,
    );
  });

  it("4: no ticket.read — even with observability.read — hides the fetch", () => {
    assert.equal(
      canViewServiceOperations(
        viewWith([CAPABILITIES.OBSERVABILITY_READ]).can,
      ),
      false,
    );
    assert.equal(canViewServiceOperations(viewWith([]).can), false);
  });

  it("5: the gate never consults a role name", () => {
    // "viewer" with ticket.read still sees the section: capability-only.
    assert.equal(
      canViewServiceOperations(viewWith([CAPABILITIES.TICKET_READ]).can),
      true,
    );
  });

  it("6: null summary maps to zero counts", () => {
    assert.deepEqual(escalationSummaryToCounts(null), {
      unacknowledged: 0,
      breached: 0,
    });
  });

  it("7: the summary maps unacknowledged + breached onto the card", () => {
    const summary: EscalationSummary = {
      total: 12,
      active: 8,
      unacknowledged: 5,
      due_soon: 3,
      breached: 2,
    };
    assert.deepEqual(escalationSummaryToCounts(summary), {
      unacknowledged: 5,
      breached: 2,
    });
  });
});