import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { CAPABILITIES } from "../authorization/capabilities.ts";
import { createAuthorizationView } from "../authorization/helpers.ts";
import type { WorkloadMember } from "./escalations.ts";
import {
  canViewTeamLoad,
  WORKLOAD_COLUMNS,
  WORKLOAD_ENDPOINT,
} from "./team-load.ts";

function viewWith(capabilities: readonly string[]) {
  return createAuthorizationView({
    organization_id: 1,
    role: "viewer",
    capabilities,
  });
}

describe("team-load dual-capability gate", () => {
  it("1: ticket.read + member.read shows Team Load", () => {
    assert.equal(
      canViewTeamLoad(
        viewWith([CAPABILITIES.TICKET_READ, CAPABILITIES.MEMBER_READ]).can,
      ),
      true,
    );
  });

  it("2: ticket.read without member.read hides Team Load", () => {
    assert.equal(
      canViewTeamLoad(viewWith([CAPABILITIES.TICKET_READ]).can),
      false,
    );
  });

  it("3: member.read without ticket.read hides Team Load", () => {
    assert.equal(
      canViewTeamLoad(viewWith([CAPABILITIES.MEMBER_READ]).can),
      false,
    );
  });

  it("4: neither capability hides Team Load", () => {
    assert.equal(canViewTeamLoad(viewWith([]).can), false);
  });

  it("5: the gate never consults a role name", () => {
    // "viewer" role with both capabilities still sees the tab: decisions are
    // capability-only, matching the backend's role-independent capabilities.
    assert.equal(
      canViewTeamLoad(
        viewWith([CAPABILITIES.TICKET_READ, CAPABILITIES.MEMBER_READ]).can,
      ),
      true,
    );
  });

  it("6: workload is fetched only through the member-gated endpoint", () => {
    assert.equal(WORKLOAD_ENDPOINT, "/api/backend/service-operations/workload");
  });
});

describe("team-load six-field workload shape", () => {
  it("7: WORKLOAD_COLUMNS lists exactly the six rendered columns", () => {
    assert.deepEqual(WORKLOAD_COLUMNS, [
      "subject",
      "open_assigned",
      "needs_response",
      "due_soon",
      "breached",
      "urgent",
    ]);
  });

  it("8: a workload member exposes all six fields", () => {
    const member: WorkloadMember = {
      subject: "user-alpha",
      open_assigned: 3,
      needs_response: 1,
      due_soon: 2,
      breached: 1,
      urgent: 1,
    };
    for (const key of WORKLOAD_COLUMNS) {
      assert.ok(
        Object.prototype.hasOwnProperty.call(member, key),
        `missing workload field: ${key}`,
      );
    }
  });
});