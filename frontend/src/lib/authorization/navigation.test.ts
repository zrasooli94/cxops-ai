import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { CAPABILITIES } from "./capabilities.ts";
import { createAuthorizationView } from "./helpers.ts";
import {
  type ControlCenterRoute,
  DASHBOARD_NAVIGATION,
  filterNavigationByCapabilities,
  isCapabilityRequirementSatisfied,
  PRIMARY_NAVIGATION,
  ROUTE_REQUIREMENTS,
} from "./navigation.ts";

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

describe("capability-driven navigation", () => {
  it("nav item with no required capability is always visible", () => {
    assert.ok(visibleHrefs([]).includes("/dashboard"));
  });

  it("nav item with a required capability is visible when present", () => {
    assert.ok(visibleHrefs([CAPABILITIES.TICKET_READ]).includes("/tickets"));
  });

  it("nav item is hidden when its capability is absent", () => {
    assert.equal(visibleHrefs([]).includes("/tickets"), false);
  });

  it("customers destination is shown only with customer.read", () => {
    assert.equal(visibleHrefs([]).includes("/customers"), false);
    assert.equal(
      visibleHrefs([CAPABILITIES.TICKET_READ]).includes("/customers"),
      false,
    );
    assert.ok(
      visibleHrefs([CAPABILITIES.CUSTOMER_READ]).includes("/customers"),
    );
  });

  it("inbox destination is shown only with ticket.read", () => {
    assert.equal(visibleHrefs([]).includes("/inbox"), false);
    assert.equal(
      visibleHrefs([CAPABILITIES.AGENT_RUN]).includes("/inbox"),
      false,
    );
    assert.ok(
      visibleHrefs([CAPABILITIES.TICKET_READ]).includes("/inbox"),
    );
  });

  it("transformation destination is shown only with ticket.read", () => {
    assert.equal(visibleHrefs([]).includes("/transformation"), false);
    assert.equal(
      visibleHrefs([CAPABILITIES.CUSTOMER_READ]).includes("/transformation"),
      false,
    );
    assert.equal(
      visibleHrefs([CAPABILITIES.AGENT_RUN]).includes("/transformation"),
      false,
    );
    assert.ok(
      visibleHrefs([CAPABILITIES.TICKET_READ]).includes("/transformation"),
    );
  });

  it("customer.write alone never reveals the customers destination", () => {
    assert.equal(
      visibleHrefs([CAPABILITIES.CUSTOMER_WRITE]).includes("/customers"),
      false,
    );
  });

  it("a viewer-like capability set shows only permitted destinations", () => {
    const visible = visibleHrefs([
      CAPABILITIES.TICKET_READ,
      CAPABILITIES.KNOWLEDGE_READ,
    ]);
    assert.deepEqual(visible, [
      "/dashboard",
      "/tickets",
      "/operations",
      "/transformation",
      "/inbox",
      "/knowledge",
    ]);
  });

  it("agent.run exposes agent, approvals, and runs", () => {
    const visible = visibleHrefs([CAPABILITIES.AGENT_RUN]);
    assert.ok(visible.includes("/agent"));
    assert.ok(visible.includes("/approvals"));
    assert.ok(visible.includes("/runs"));
  });

  it("observability is shown only with observability.read", () => {
    assert.equal(visibleHrefs([]).includes("/observability"), false);
    assert.equal(
      visibleHrefs([CAPABILITIES.AGENT_RUN]).includes("/observability"),
      false,
    );
    assert.ok(
      visibleHrefs([CAPABILITIES.OBSERVABILITY_READ]).includes("/observability"),
    );
  });

  it("evaluation.read reveals the evaluations destination", () => {
    assert.equal(visibleHrefs([]).includes("/evaluations"), false);
    assert.equal(
      visibleHrefs([CAPABILITIES.TICKET_READ]).includes("/evaluations"),
      false,
    );
    assert.ok(
      visibleHrefs([CAPABILITIES.EVALUATION_READ]).includes("/evaluations"),
    );
  });

  it("evaluation.manage alone never reveals the evaluations destination", () => {
    assert.equal(
      visibleHrefs([CAPABILITIES.EVALUATION_MANAGE]).includes("/evaluations"),
      false,
    );
  });

  it("an unknown capability string never unlocks a destination", () => {
    assert.equal(
      visibleHrefs(["ticket.read.everything"]).includes("/tickets"),
      false,
    );
  });

  it("switching organizations does not reuse the previous capability set", () => {
    const orgA = createAuthorizationView({
      organization_id: 1,
      role: "admin",
      capabilities: [CAPABILITIES.AGENT_RUN, CAPABILITIES.OBSERVABILITY_READ],
    });
    const orgB = createAuthorizationView({
      organization_id: 2,
      role: "viewer",
      capabilities: [],
    });

    const orgAVisible = filterNavigationByCapabilities(
      PRIMARY_NAVIGATION,
      orgA.can,
    ).map((item) => item.href);
    const orgBVisible = filterNavigationByCapabilities(
      PRIMARY_NAVIGATION,
      orgB.can,
    ).map((item) => item.href);

    assert.ok(orgAVisible.includes("/agent"));
    assert.equal(orgBVisible.includes("/agent"), false);
    assert.deepEqual(orgBVisible, ["/dashboard"]);
  });

  it("dashboard cards exclude the dashboard and share capability rules", () => {
    assert.equal(
      DASHBOARD_NAVIGATION.some((item) => item.href === "/dashboard"),
      false,
    );

    const view = createAuthorizationView({
      organization_id: 1,
      role: "viewer",
      capabilities: [CAPABILITIES.TICKET_READ],
    });
    const cards = filterNavigationByCapabilities(
      DASHBOARD_NAVIGATION,
      view.can,
    ).map((item) => item.href);

    assert.deepEqual(cards, [
      "/tickets",
      "/operations",
      "/transformation",
      "/inbox",
    ]);
  });

  it("every requirement is a known capability or an explicit always-visible null", () => {
    const known = new Set<string>(Object.values(CAPABILITIES));
    for (const item of PRIMARY_NAVIGATION) {
      assert.ok(
        item.requiredCapability === null ||
          known.has(item.requiredCapability),
        `${item.href} has an unknown requirement`,
      );
    }
  });
});

describe("control-center route requirements", () => {
  const expected: ReadonlyArray<[ControlCenterRoute, string | null]> = [
    ["/dashboard", null],
    ["/customers", CAPABILITIES.CUSTOMER_READ],
    ["/tickets", CAPABILITIES.TICKET_READ],
    ["/tickets/new", CAPABILITIES.TICKET_WRITE],
    ["/inbox", CAPABILITIES.TICKET_READ],
    ["/agent", CAPABILITIES.AGENT_RUN],
    ["/approvals", CAPABILITIES.AGENT_RUN],
    ["/knowledge", CAPABILITIES.KNOWLEDGE_READ],
    ["/runs", CAPABILITIES.AGENT_RUN],
    ["/observability", CAPABILITIES.OBSERVABILITY_READ],
    ["/operations", CAPABILITIES.TICKET_READ],
    ["/evaluations", CAPABILITIES.EVALUATION_READ],
    ["/transformation", CAPABILITIES.TICKET_READ],
  ];

  for (const [route, requirement] of expected) {
    it(`route ${route} requires ${requirement ?? "authentication only"}`, () => {
      assert.equal(ROUTE_REQUIREMENTS[route], requirement);
    });
  }

  it("every route requirement is a known capability or an explicit null", () => {
    const known = new Set<string>(Object.values(CAPABILITIES));
    for (const requirement of Object.values(ROUTE_REQUIREMENTS)) {
      assert.ok(requirement === null || known.has(requirement));
    }
  });

  it("sidebar visibility and route guards share the same requirement", () => {
    for (const item of PRIMARY_NAVIGATION) {
      assert.equal(item.requiredCapability, ROUTE_REQUIREMENTS[item.href]);
    }
  });

  it("a null requirement is satisfied with no capabilities at all", () => {
    assert.equal(isCapabilityRequirementSatisfied(null, () => false), true);
  });

  it("a capability requirement is satisfied only when the capability is present", () => {
    const can = (capability: string) =>
      capability === CAPABILITIES.TICKET_READ;
    assert.equal(
      isCapabilityRequirementSatisfied(CAPABILITIES.TICKET_READ, can),
      true,
    );
    assert.equal(
      isCapabilityRequirementSatisfied(CAPABILITIES.TICKET_WRITE, can),
      false,
    );
  });

  it("an unknown capability never authorizes a guarded route", () => {
    const can = (capability: string) => capability === "ticket.read.everything";
    assert.equal(
      isCapabilityRequirementSatisfied(CAPABILITIES.TICKET_READ, can),
      false,
    );
  });
});
