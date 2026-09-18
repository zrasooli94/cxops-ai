import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  CAPABILITIES,
  type Capability,
} from "../authorization/capabilities.ts";
import {
  createAuthorizationView,
  PERMISSION_DENIED_FEEDBACK,
  planClientAuthorizationResponse,
} from "../authorization/helpers.ts";
import {
  filterNavigationByCapabilities,
  PRIMARY_NAVIGATION,
  ROUTE_REQUIREMENTS,
} from "../authorization/navigation.ts";
import {
  deriveCustomerExperience,
  type CustomerExperience,
} from "./customer.ts";

function experienceFor(capabilities: readonly string[]): CustomerExperience {
  const view = createAuthorizationView({
    organization_id: 1,
    role: "viewer",
    capabilities,
  });
  return deriveCustomerExperience(view.can);
}

function visibleHrefs(capabilities: readonly string[]): string[] {
  const view = createAuthorizationView({
    organization_id: 1,
    role: "viewer",
    capabilities,
  });
  return filterNavigationByCapabilities(PRIMARY_NAVIGATION, view.can).map(
    (item) => item.href,
  );
}

describe("customer 360 authorization experience", () => {
  it("1. the /customers route requires customer.read", () => {
    assert.equal(ROUTE_REQUIREMENTS["/customers"], CAPABILITIES.CUSTOMER_READ);
  });

  it("2. the customers destination is hidden without customer.read", () => {
    assert.equal(visibleHrefs([]).includes("/customers"), false);
    assert.equal(
      visibleHrefs([CAPABILITIES.TICKET_READ]).includes("/customers"),
      false,
    );
  });

  it("3. the customers destination is visible with customer.read", () => {
    assert.ok(
      visibleHrefs([CAPABILITIES.CUSTOMER_READ]).includes("/customers"),
    );
  });

  it("4. customer.write derives the profile-edit affordance", () => {
    const readOnly = experienceFor([CAPABILITIES.CUSTOMER_READ]);
    assert.equal(readOnly.canEditProfile, false);
    assert.equal(readOnly.readOnly, true);

    const writable = experienceFor([
      CAPABILITIES.CUSTOMER_READ,
      CAPABILITIES.CUSTOMER_WRITE,
    ]);
    assert.equal(writable.canWrite, true);
    assert.equal(writable.canEditProfile, true);
    assert.equal(writable.readOnly, false);
  });

  it("5. a role string alone never derives any permission", () => {
    const view = createAuthorizationView({
      organization_id: 1,
      role: "owner",
      capabilities: [],
    });
    const experience = deriveCustomerExperience(view.can);
    assert.equal(experience.canRead, false);
    assert.equal(experience.canWrite, false);
    assert.equal(experience.canViewTickets, false);
    assert.equal(experience.canViewAgentActivity, false);
    assert.equal(experience.canEditProfile, false);
  });

  it("6. the linked-tickets section is derived only from ticket.read", () => {
    const withoutTickets = experienceFor([CAPABILITIES.CUSTOMER_READ]);
    assert.equal(withoutTickets.canViewTickets, false);

    const withTickets = experienceFor([
      CAPABILITIES.CUSTOMER_READ,
      CAPABILITIES.TICKET_READ,
    ]);
    assert.equal(withTickets.canViewTickets, true);
  });

  it("7. agent activity is derived only from agent.run", () => {
    const withoutAgent = experienceFor([
      CAPABILITIES.CUSTOMER_READ,
      CAPABILITIES.TICKET_READ,
    ]);
    assert.equal(withoutAgent.canViewAgentActivity, false);

    const withAgent = experienceFor([
      CAPABILITIES.CUSTOMER_READ,
      CAPABILITIES.AGENT_RUN,
    ]);
    assert.equal(withAgent.canViewAgentActivity, true);
  });

  it("8. switching organizations does not reuse the previous capability set", () => {
    const orgA = createAuthorizationView({
      organization_id: 1,
      role: "admin",
      capabilities: [
        CAPABILITIES.CUSTOMER_READ,
        CAPABILITIES.CUSTOMER_WRITE,
        CAPABILITIES.AGENT_RUN,
      ],
    });
    const orgB = createAuthorizationView({
      organization_id: 2,
      role: "viewer",
      capabilities: [],
    });

    const orgAExperience = deriveCustomerExperience(orgA.can);
    const orgBExperience = deriveCustomerExperience(orgB.can);

    assert.equal(orgAExperience.canRead, true);
    assert.equal(orgAExperience.canEditProfile, true);
    assert.equal(orgAExperience.canViewAgentActivity, true);

    assert.equal(orgBExperience.canRead, false);
    assert.equal(orgBExperience.canEditProfile, false);
    assert.equal(orgBExperience.canViewAgentActivity, false);
  });

  it("9. a capability denial plans a refresh and never a retry", () => {
    const plan = planClientAuthorizationResponse(403, "Insufficient permissions");
    assert.equal(plan.kind, "capability-denied");
    assert.equal(plan.refreshAuthorization, true);
    assert.equal(plan.recoverTenant, false);
    assert.equal("retry" in plan, false);
    assert.equal(
      PERMISSION_DENIED_FEEDBACK,
      "You don't have permission to perform this action in this organization.",
    );
  });

  it("10. a membership failure routes to tenant recovery", () => {
    const plan = planClientAuthorizationResponse(
      403,
      "Organization membership not found",
    );
    assert.equal(plan.kind, "membership-invalid");
    assert.equal(plan.recoverTenant, true);
    assert.equal(plan.refreshAuthorization, false);
  });

  it("11. an unknown capability string never satisfies a requirement", () => {
    const can = (capability: Capability) =>
      capability === ("customer.read.everything" as Capability);
    const experience = deriveCustomerExperience(can);
    assert.equal(experience.canRead, false);
    assert.equal(experience.canEditProfile, false);
    assert.equal(visibleHrefs(["customer.read.everything"]).includes("/customers"), false);
  });
});
