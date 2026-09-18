import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { TENANT_SELECTOR_HEADER } from "../auth/proxy-headers.ts";
import { CAPABILITIES } from "./capabilities.ts";
import {
  AUTHORIZATION_PATH,
  AuthorizationLoadError,
  authorizationFeedback,
  classifyAuthorizationFailure,
  classifyClientAuthorizationUx,
  createAuthorizationView,
  deriveKnowledgeExperience,
  deriveTicketExperience,
  hasAllCapabilities,
  hasAnyCapability,
  hasCapability,
  isCapabilityForbidden,
  isMembershipAuthorizationError,
  isOrganizationSelectionConflict,
  loadAuthorization,
  ORGANIZATION_CHANGED_FEEDBACK,
  parseAuthorizationInfo,
  PERMISSION_DENIED_FEEDBACK,
  planClientAuthorizationResponse,
  readErrorDetail,
  shouldClearOrganizationSelection,
  tenantSelectorHeaderValue,
} from "./helpers.ts";

const validPayload = {
  organization_id: 42,
  role: "supervisor",
  capabilities: ["ticket.read", "ticket.write", "agent.run"],
};

function okResponse(payload: unknown) {
  return {
    status: 200,
    ok: true,
    json: async () => payload,
  };
}

describe("capability helpers", () => {
  it("1: exact capability match returns true", () => {
    assert.equal(hasCapability(["ticket.read"], "ticket.read"), true);
  });

  it("2: different capability returns false", () => {
    assert.equal(hasCapability(["ticket.read"], "ticket.write"), false);
  });

  it("3: hasAny returns true when one requirement matches", () => {
    assert.equal(
      hasAnyCapability(["ticket.read"], ["ticket.write", "ticket.read"]),
      true,
    );
  });

  it("4: hasAny returns false when nothing matches", () => {
    assert.equal(
      hasAnyCapability(["ticket.read"], ["ticket.write", "agent.run"]),
      false,
    );
  });

  it("5: hasAll returns true only when every requirement matches", () => {
    assert.equal(
      hasAllCapabilities(
        ["ticket.read", "ticket.write"],
        ["ticket.read", "ticket.write"],
      ),
      true,
    );
    assert.equal(
      hasAllCapabilities(["ticket.read"], ["ticket.read", "ticket.write"]),
      false,
    );
  });

  it("6: empty capabilities fail safely", () => {
    assert.equal(hasCapability([], "ticket.read"), false);
    assert.equal(hasAnyCapability([], ["ticket.read"]), false);
    assert.equal(hasAllCapabilities([], ["ticket.read"]), false);
  });

  it("6b: an empty requirement fails closed", () => {
    assert.equal(hasAllCapabilities([], []), false);
    assert.equal(hasAllCapabilities(["ticket.read"], []), false);
  });

  it("7: an unknown capability string cannot accidentally authorize", () => {
    assert.equal(hasCapability(["ticket.read"], "ticket.read.everything"), false);
    assert.equal(
      hasAllCapabilities(["ticket.read"], ["ticket.read.unknown"]),
      false,
    );
  });

  it("8: CAPABILITIES constants reflect the backend contract", () => {
    assert.equal(CAPABILITIES.TICKET_READ, "ticket.read");
    assert.equal(CAPABILITIES.AGENT_EXECUTE, "agent.execute");
    assert.equal(CAPABILITIES.OBSERVABILITY_READ, "observability.read");
  });
});

describe("backend authorization error classification", () => {
  it("9: capability denial is classified as a capability failure", () => {
    assert.equal(isCapabilityForbidden("Insufficient permissions"), true);
    assert.equal(
      classifyAuthorizationFailure(403, "Insufficient permissions"),
      "capability",
    );
  });

  it("10: a capability denial is not a membership failure", () => {
    assert.equal(
      isMembershipAuthorizationError("Insufficient permissions"),
      false,
    );
  });

  it("11: membership not found is a membership failure", () => {
    assert.equal(
      isMembershipAuthorizationError("Organization membership not found"),
      true,
    );
    assert.equal(
      classifyAuthorizationFailure(
        403,
        "Organization membership not found",
      ),
      "membership",
    );
  });

  it("12: no active organization membership is a membership failure", () => {
    assert.equal(
      isMembershipAuthorizationError("No active organization membership"),
      true,
    );
  });

  it("13: invalid organization selection is a membership/tenant failure", () => {
    assert.equal(
      isMembershipAuthorizationError("Invalid organization selection"),
      true,
    );
  });

  it("14: an unknown 403 detail is not treated as membership revocation", () => {
    assert.equal(isMembershipAuthorizationError("Something else went wrong"), false);
    assert.equal(
      classifyAuthorizationFailure(403, "Something else went wrong"),
      "unknown",
    );
  });

  it("15: the multiple-memberships 409 is an organization-selection conflict", () => {
    const detail =
      "Multiple organization memberships configured; select one via X-CXOps-Organization-ID";
    assert.equal(isOrganizationSelectionConflict(detail), true);
    assert.equal(classifyAuthorizationFailure(409, detail), "organization-selection");
  });

  it("16: an unrelated 409 is not an organization-selection conflict", () => {
    assert.equal(
      isOrganizationSelectionConflict("Agent run has already been executed."),
      false,
    );
    assert.equal(
      classifyAuthorizationFailure(409, "Agent run has already been executed."),
      "unknown",
    );
  });

  it("17: readErrorDetail extracts only well-formed JSON details", () => {
    assert.equal(
      readErrorDetail('{"detail":"Insufficient permissions"}', "application/json"),
      "Insufficient permissions",
    );
    assert.equal(readErrorDetail("not json", "application/json"), null);
    assert.equal(readErrorDetail('{"detail":123}', "application/json"), null);
    assert.equal(readErrorDetail('{"detail":"x"}', "text/plain"), null);
    assert.equal(readErrorDetail('{"detail":"x"}', null), null);
  });
});

describe("organization selection clearing decision", () => {
  it("18: capability 403 preserves the organization selection", () => {
    assert.equal(
      shouldClearOrganizationSelection(403, "Insufficient permissions"),
      false,
    );
  });

  it("19: membership 403 keeps recovery behavior", () => {
    assert.equal(
      shouldClearOrganizationSelection(
        403,
        "Organization membership not found",
      ),
      true,
    );
    assert.equal(
      shouldClearOrganizationSelection(403, "No active organization membership"),
      true,
    );
  });

  it("20: selector 409 keeps recovery behavior", () => {
    assert.equal(
      shouldClearOrganizationSelection(
        409,
        "Multiple organization memberships configured; select one via X-CXOps-Organization-ID",
      ),
      true,
    );
  });

  it("21: unknown 403 does not destroy tenant state", () => {
    assert.equal(
      shouldClearOrganizationSelection(403, "Unexpected backend detail"),
      false,
    );
  });

  it("22: unrelated 409 does not destroy tenant state", () => {
    assert.equal(
      shouldClearOrganizationSelection(409, "Agent run has already been executed."),
      false,
    );
  });
});

describe("authorization derivation", () => {
  const orgA = createAuthorizationView({
    organization_id: 1,
    role: "viewer",
    capabilities: ["ticket.read"],
  });
  const orgB = createAuthorizationView({
    organization_id: 2,
    role: "admin",
    capabilities: ["ticket.read", "ticket.write"],
  });

  it("23: organization capability sets are independent", () => {
    assert.equal(orgA.can(CAPABILITIES.TICKET_READ), true);
    assert.equal(orgA.can(CAPABILITIES.TICKET_WRITE), false);
    assert.equal(orgB.can(CAPABILITIES.TICKET_WRITE), true);
    assert.equal(orgA.organizationId, 1);
    assert.equal(orgB.organizationId, 2);
  });

  it("24: a role string alone does not grant permission", () => {
    const ownerWithoutCapabilities = createAuthorizationView({
      organization_id: 3,
      role: "owner",
      capabilities: [],
    });
    assert.equal(ownerWithoutCapabilities.can(CAPABILITIES.ORGANIZATION_MANAGE), false);
  });

  it("25: results change only when the capability set changes", () => {
    const beforeRoleChange = createAuthorizationView({
      organization_id: 4,
      role: "agent",
      capabilities: ["agent.run"],
    });
    const afterRoleChange = createAuthorizationView({
      organization_id: 4,
      role: "supervisor",
      capabilities: ["agent.run"],
    });
    const afterCapabilityChange = createAuthorizationView({
      organization_id: 4,
      role: "supervisor",
      capabilities: ["agent.run", "agent.approve"],
    });

    assert.equal(beforeRoleChange.can(CAPABILITIES.AGENT_APPROVE), false);
    assert.equal(afterRoleChange.can(CAPABILITIES.AGENT_APPROVE), false);
    assert.equal(afterCapabilityChange.can(CAPABILITIES.AGENT_APPROVE), true);
  });
});

describe("authorization backend contract", () => {
  it("26: AUTHORIZATION_PATH targets /me/authorization", () => {
    assert.equal(AUTHORIZATION_PATH, "/me/authorization");
  });

  it("27: the request uses the supplied organization id", async () => {
    const seen: number[] = [];
    const result = await loadAuthorization(async (organizationId) => {
      seen.push(organizationId);
      return okResponse({ ...validPayload, organization_id: organizationId });
    }, 77);

    assert.deepEqual(seen, [77]);
    assert.equal(result.organization_id, 77);
  });

  it("28: the tenant selector header is stable and serialized from the org id", () => {
    assert.equal(TENANT_SELECTOR_HEADER, "x-cxops-organization-id");
    assert.equal(tenantSelectorHeaderValue(42), "42");
  });

  it("29: a valid 200 response maps to AuthorizationInfo", async () => {
    const result = await loadAuthorization(
      async () => okResponse(validPayload),
      42,
    );
    assert.deepEqual(result, {
      organization_id: 42,
      role: "supervisor",
      capabilities: ["ticket.read", "ticket.write", "agent.run"],
    });
  });

  it("30: malformed responses fail safely", async () => {
    await assert.rejects(
      () =>
        loadAuthorization(
          async () => okResponse({ organization_id: 42, role: "supervisor" }),
          42,
        ),
      /Invalid authorization response/,
    );

    await assert.rejects(
      () =>
        loadAuthorization(
          async () => ({
            status: 200,
            ok: true,
            json: async () => {
              throw new Error("bad json");
            },
          }),
          42,
        ),
      /Invalid authorization response/,
    );

    assert.equal(parseAuthorizationInfo(null), null);
    assert.equal(parseAuthorizationInfo({}), null);
    assert.equal(
      parseAuthorizationInfo({
        organization_id: 0,
        role: "viewer",
        capabilities: [],
      }),
      null,
    );
    assert.equal(
      parseAuthorizationInfo({
        organization_id: 1,
        role: "viewer",
        capabilities: [1, 2],
      }),
      null,
    );
  });

  it("31: a 401 follows existing session-expiry behavior", async () => {
    await assert.rejects(
      () =>
        loadAuthorization(
          async () => ({ status: 401, ok: false, json: async () => ({}) }),
          42,
        ),
      /Authentication required/,
    );
  });

  it("32: a mismatched organization id fails closed", async () => {
    await assert.rejects(
      () =>
        loadAuthorization(
          async () => okResponse({ ...validPayload, organization_id: 99 }),
          42,
        ),
      (error: unknown) => {
        assert.ok(error instanceof AuthorizationLoadError);
        assert.equal(error.reason, "mismatch");
        assert.match(error.message, /Invalid authorization response/);
        return true;
      },
    );
  });

  it("33: a 403 on the authorization endpoint is a membership failure", async () => {
    await assert.rejects(
      () =>
        loadAuthorization(
          async () => ({ status: 403, ok: false, json: async () => ({}) }),
          42,
        ),
      (error: unknown) => {
        assert.ok(error instanceof AuthorizationLoadError);
        assert.equal(error.reason, "membership");
        return true;
      },
    );
  });

  it("34: a backend 5xx is an unavailable failure, not a membership failure", async () => {
    await assert.rejects(
      () =>
        loadAuthorization(
          async () => ({ status: 503, ok: false, json: async () => ({}) }),
          42,
        ),
      (error: unknown) => {
        assert.ok(error instanceof AuthorizationLoadError);
        assert.equal(error.reason, "unavailable");
        return true;
      },
    );
  });

  it("35: a malformed 200 is a malformed failure", async () => {
    await assert.rejects(
      () =>
        loadAuthorization(
          async () => okResponse({ organization_id: 42, role: "viewer" }),
          42,
        ),
      (error: unknown) => {
        assert.ok(error instanceof AuthorizationLoadError);
        assert.equal(error.reason, "malformed");
        return true;
      },
    );
  });
});

describe("client authorization response planning", () => {
  const forbidden = "Insufficient permissions";
  const membership = "Organization membership not found";
  const selector =
    "Multiple organization memberships configured; select one via X-CXOps-Organization-ID";

  it("36: a capability 403 maps to capability-denied", () => {
    assert.equal(classifyClientAuthorizationUx(403, forbidden), "capability-denied");
  });

  it("37: a capability 403 refreshes authorization but never recovers the tenant", () => {
    const plan = planClientAuthorizationResponse(403, forbidden);
    assert.equal(plan.kind, "capability-denied");
    assert.equal(plan.refreshAuthorization, true);
    assert.equal(plan.recoverTenant, false);
  });

  it("38: a membership 403 maps to membership-invalid", () => {
    assert.equal(
      classifyClientAuthorizationUx(403, membership),
      "membership-invalid",
    );
  });

  it("39: a membership 403 recovers the tenant instead of refreshing capabilities", () => {
    const plan = planClientAuthorizationResponse(403, membership);
    assert.equal(plan.kind, "membership-invalid");
    assert.equal(plan.recoverTenant, true);
    assert.equal(plan.refreshAuthorization, false);
  });

  it("40: a selector 409 maps to membership-invalid and recovers the tenant", () => {
    const plan = planClientAuthorizationResponse(409, selector);
    assert.equal(plan.kind, "membership-invalid");
    assert.equal(plan.recoverTenant, true);
  });

  it("41: an unknown 403 maps to other and triggers no authorization recovery", () => {
    const plan = planClientAuthorizationResponse(403, "Unexpected backend detail");
    assert.equal(plan.kind, "other");
    assert.equal(plan.recoverTenant, false);
    assert.equal(plan.refreshAuthorization, false);
  });

  it("42: an unrelated 409 stays other, not a membership recovery", () => {
    const plan = planClientAuthorizationResponse(
      409,
      "Agent run has already been executed.",
    );
    assert.equal(plan.kind, "other");
    assert.equal(plan.recoverTenant, false);
  });

  it("43: a plan never carries an automatic-retry signal", () => {
    const plan = planClientAuthorizationResponse(403, forbidden);
    assert.deepEqual(Object.keys(plan).sort(), [
      "kind",
      "recoverTenant",
      "refreshAuthorization",
    ]);
    assert.equal("retry" in (plan as unknown as Record<string, unknown>), false);
  });

  it("44: capability denial uses friendly, non-leaky feedback", () => {
    const feedback = authorizationFeedback(
      planClientAuthorizationResponse(403, forbidden),
    );
    assert.equal(feedback, PERMISSION_DENIED_FEEDBACK);
    assert.notEqual(feedback, forbidden);
  });

  it("45: membership invalidation uses friendly, non-leaky feedback", () => {
    const feedback = authorizationFeedback(
      planClientAuthorizationResponse(403, membership),
    );
    assert.equal(feedback, ORGANIZATION_CHANGED_FEEDBACK);
    assert.notEqual(feedback, membership);
  });

  it("46: a non-authorization failure has no authorization feedback", () => {
    assert.equal(
      authorizationFeedback(
        planClientAuthorizationResponse(500, "Internal Server Error"),
      ),
      null,
    );
  });
});

describe("ticket and knowledge experience derivation", () => {
  it("47: ticket.read without ticket.write is read-only", () => {
    const view = createAuthorizationView({
      organization_id: 1,
      role: "viewer",
      capabilities: [CAPABILITIES.TICKET_READ],
    });
    const experience = deriveTicketExperience(view.can);
    assert.equal(experience.canRead, true);
    assert.equal(experience.canWrite, false);
    assert.equal(experience.readOnly, true);
  });

  it("48: ticket.write enables write affordances", () => {
    const view = createAuthorizationView({
      organization_id: 1,
      role: "supervisor",
      capabilities: [CAPABILITIES.TICKET_READ, CAPABILITIES.TICKET_WRITE],
    });
    const experience = deriveTicketExperience(view.can);
    assert.equal(experience.canWrite, true);
    assert.equal(experience.readOnly, false);
  });

  it("49: a role name alone never grants ticket.write", () => {
    const view = createAuthorizationView({
      organization_id: 1,
      role: "owner",
      capabilities: [CAPABILITIES.TICKET_READ],
    });
    assert.equal(deriveTicketExperience(view.can).canWrite, false);
  });

  it("50: knowledge.read without knowledge.manage is read-only", () => {
    const view = createAuthorizationView({
      organization_id: 1,
      role: "viewer",
      capabilities: [CAPABILITIES.KNOWLEDGE_READ],
    });
    const experience = deriveKnowledgeExperience(view.can);
    assert.equal(experience.canManage, false);
    assert.equal(experience.readOnly, true);
  });

  it("51: knowledge.manage enables management controls", () => {
    const view = createAuthorizationView({
      organization_id: 1,
      role: "admin",
      capabilities: [CAPABILITIES.KNOWLEDGE_READ, CAPABILITIES.KNOWLEDGE_MANAGE],
    });
    const experience = deriveKnowledgeExperience(view.can);
    assert.equal(experience.canManage, true);
    assert.equal(experience.readOnly, false);
  });

  it("52: a role name alone never grants knowledge.manage", () => {
    const view = createAuthorizationView({
      organization_id: 1,
      role: "admin",
      capabilities: [CAPABILITIES.KNOWLEDGE_READ],
    });
    assert.equal(deriveKnowledgeExperience(view.can).canManage, false);
  });
});
