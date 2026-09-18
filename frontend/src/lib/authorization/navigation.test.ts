import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { CAPABILITIES } from "./capabilities.ts";
import { createAuthorizationView } from "./helpers.ts";
import {
  DASHBOARD_NAVIGATION,
  filterNavigationByCapabilities,
  PRIMARY_NAVIGATION,
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

  it("a viewer-like capability set shows only permitted destinations", () => {
    const visible = visibleHrefs([
      CAPABILITIES.TICKET_READ,
      CAPABILITIES.KNOWLEDGE_READ,
    ]);
    assert.deepEqual(visible, ["/dashboard", "/tickets", "/knowledge"]);
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

    assert.deepEqual(cards, ["/tickets"]);
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
