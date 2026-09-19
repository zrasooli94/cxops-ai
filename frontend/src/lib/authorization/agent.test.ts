import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  approvalQueuedMessage,
  deriveAgentExperience,
  deriveAgentRunActions,
  executionQueuedMessage,
  isRunExecutableStatus,
  planPartialExecutionFailure,
  PARTIAL_EXECUTION_CAPABILITY_FEEDBACK,
  PARTIAL_EXECUTION_FEEDBACK,
  PARTIAL_EXECUTION_MEMBERSHIP_FEEDBACK,
} from "./agent.ts";
import { CAPABILITIES } from "./capabilities.ts";
import {
  createAuthorizationView,
  planClientAuthorizationResponse,
  authorizationFeedback,
  PERMISSION_DENIED_FEEDBACK,
} from "./helpers.ts";

/** Build a `can` predicate from a raw capability list — never from role. */
function canFrom(capabilities: readonly string[]) {
  const view = createAuthorizationView({
    organization_id: 1,
    role: "ignored",
    capabilities,
  });
  return view.can;
}

const RUN_APPROVED = {
  action: "respond",
  status: "approved",
  external_execution_available: true,
};
const RUN_FAILED = {
  action: "respond",
  status: "execution_failed",
  external_execution_available: true,
};
const RUN_HUMAN_REVIEW_APPROVED = {
  action: "human_review",
  status: "approved",
  external_execution_available: true,
};
const RUN_PENDING = {
  action: "respond",
  status: "pending_approval",
  external_execution_available: true,
};
const RUN_PENDING_HUMAN_REVIEW = {
  action: "human_review",
  status: "pending_approval",
  external_execution_available: true,
};
const RUN_REVIEW_REQUIRED = {
  action: "respond",
  status: "review_required",
  external_execution_available: true,
};
const RUN_PENDING_LOCAL_DEMO = {
  action: "respond",
  status: "pending_approval",
  external_execution_available: false,
};

describe("agent experience derivation", () => {
  it("1: agent.run only yields canRun, with no approve or execute", () => {
    const agent = deriveAgentExperience(canFrom([CAPABILITIES.AGENT_RUN]));
    assert.equal(agent.canRun, true);
    assert.equal(agent.canApprove, false);
    assert.equal(agent.canExecute, false);
  });

  it("2: agent.approve does not imply agent.execute", () => {
    const agent = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_APPROVE]),
    );
    assert.equal(agent.canApprove, true);
    assert.equal(agent.canExecute, false);
  });

  it("3: agent.execute does not imply agent.approve", () => {
    const agent = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_EXECUTE]),
    );
    assert.equal(agent.canExecute, true);
    assert.equal(agent.canApprove, false);
  });

  it("4: role string never changes any Agent decision", () => {
    const capabilities = [CAPABILITIES.AGENT_RUN];
    const low = deriveAgentExperience(
      createAuthorizationView({
        organization_id: 1,
        role: "viewer",
        capabilities,
      }).can,
    );
    const high = deriveAgentExperience(
      createAuthorizationView({
        organization_id: 1,
        role: "owner",
        capabilities,
      }).can,
    );
    assert.deepEqual(low, high);
    assert.equal(high.canApprove, false);
    assert.equal(high.canExecute, false);
  });

  it("5: unknown capability never grants an Agent action", () => {
    const agent = deriveAgentExperience(
      canFrom(["agent.superuser", "ticket.write"]),
    );
    assert.equal(agent.canRun, false);
    assert.equal(agent.canApprove, false);
    assert.equal(agent.canExecute, false);
  });
});

describe("approval UX derivation", () => {
  const runOnly = deriveAgentExperience(canFrom([CAPABILITIES.AGENT_RUN]));

  it("6: agent.run only produces a read-only approval queue plan", () => {
    const plan = deriveAgentRunActions(RUN_PENDING, runOnly);
    assert.equal(plan.canApproveRun, false);
    assert.equal(plan.canRejectRun, false);
    assert.equal(plan.canApproveAndExecute, false);
    assert.equal(plan.canExecuteRun, false);
  });

  it("7: agent.approve enables approve", () => {
    const agent = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_APPROVE]),
    );
    assert.equal(deriveAgentRunActions(RUN_PENDING, agent).canApproveRun, true);
  });

  it("8: agent.approve enables reject", () => {
    const agent = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_APPROVE]),
    );
    assert.equal(deriveAgentRunActions(RUN_PENDING, agent).canRejectRun, true);
  });

  it("9: agent.approve without agent.execute produces Approve-only", () => {
    const agent = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_APPROVE]),
    );
    const plan = deriveAgentRunActions(RUN_PENDING, agent);
    assert.equal(plan.canApproveRun, true);
    assert.equal(plan.canApproveAndExecute, false);
  });

  it("10: agent.approve + agent.execute produces Approve & Queue when appropriate", () => {
    const agent = deriveAgentExperience(
      canFrom([
        CAPABILITIES.AGENT_RUN,
        CAPABILITIES.AGENT_APPROVE,
        CAPABILITIES.AGENT_EXECUTE,
      ]),
    );
    assert.equal(
      deriveAgentRunActions(RUN_PENDING, agent).canApproveAndExecute,
      true,
    );
  });

  it("11: human_review never produces execution after approval", () => {
    const agent = deriveAgentExperience(
      canFrom([
        CAPABILITIES.AGENT_RUN,
        CAPABILITIES.AGENT_APPROVE,
        CAPABILITIES.AGENT_EXECUTE,
      ]),
    );
    const plan = deriveAgentRunActions(RUN_HUMAN_REVIEW_APPROVED, agent);
    assert.equal(plan.canApproveRun, false);
    assert.equal(plan.canApproveAndExecute, false);
    assert.equal(plan.canExecuteRun, false);
  });

  it("11b: pending human_review can be approved (sent for review)", () => {
    const agent = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_APPROVE]),
    );
    const plan = deriveAgentRunActions(RUN_PENDING_HUMAN_REVIEW, agent);
    assert.equal(plan.canApproveRun, true);
    assert.equal(plan.canRejectRun, true);
    assert.equal(plan.canApproveAndExecute, false);
    assert.equal(plan.canExecuteRun, false);
  });

  it("11c: review_required runs can only be marked reviewed", () => {
    const agent = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_APPROVE]),
    );
    const plan = deriveAgentRunActions(RUN_REVIEW_REQUIRED, agent);
    assert.equal(plan.canApproveRun, false);
    assert.equal(plan.canRejectRun, false);
    assert.equal(plan.canReviewRun, true);
    assert.equal(plan.canApproveAndExecute, false);
    assert.equal(plan.canExecuteRun, false);
  });

  it("11d: review_required without agent.approve is read-only", () => {
    const plan = deriveAgentRunActions(
      RUN_REVIEW_REQUIRED,
      deriveAgentExperience(canFrom([CAPABILITIES.AGENT_RUN])),
    );
    assert.equal(plan.canReviewRun, false);
  });

  it("12: missing agent.approve never enables reject", () => {
    const agent = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_EXECUTE]),
    );
    assert.equal(deriveAgentRunActions(RUN_PENDING, agent).canRejectRun, false);
  });
});

describe("execution UX derivation", () => {
  const executor = deriveAgentExperience(
    canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_EXECUTE]),
  );

  it("13: agent.execute permits UI execution only for a backend-supported state", () => {
    assert.equal(deriveAgentRunActions(RUN_APPROVED, executor).canExecuteRun, true);
    assert.equal(deriveAgentRunActions(RUN_FAILED, executor).canExecuteRun, true);
    assert.equal(deriveAgentRunActions(RUN_PENDING, executor).canExecuteRun, false);
    assert.equal(
      deriveAgentRunActions({ action: "respond", status: "executed" }, executor)
        .canExecuteRun,
      false,
    );
  });

  it("13b: external_execution_available=false hides execute/retry", () => {
    const localDemoApproved = {
      ...RUN_APPROVED,
      external_execution_available: false,
    };
    const localDemoFailed = {
      ...RUN_FAILED,
      external_execution_available: false,
    };
    assert.equal(
      deriveAgentRunActions(localDemoApproved, executor).canExecuteRun,
      false,
    );
    assert.equal(
      deriveAgentRunActions(localDemoFailed, executor).canExecuteRun,
      false,
    );
  });

  it("13c: local/demo pending approval cannot approve-and-execute", () => {
    const agent = deriveAgentExperience(
      canFrom([
        CAPABILITIES.AGENT_RUN,
        CAPABILITIES.AGENT_APPROVE,
        CAPABILITIES.AGENT_EXECUTE,
      ]),
    );
    const plan = deriveAgentRunActions(RUN_PENDING_LOCAL_DEMO, agent);
    assert.equal(plan.canApproveRun, true);
    assert.equal(plan.canApproveAndExecute, false);
    assert.equal(plan.canExecuteRun, false);
  });

  it("14: absence of agent.execute disables the execution action", () => {
    const approver = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN, CAPABILITIES.AGENT_APPROVE]),
    );
    assert.equal(deriveAgentRunActions(RUN_APPROVED, approver).canExecuteRun, false);
  });

  it("15: execution_failed derives Retry execution only when state is supported", () => {
    assert.equal(isRunExecutableStatus("execution_failed"), true);
    assert.equal(deriveAgentRunActions(RUN_FAILED, executor).canExecuteRun, true);
    assert.equal(
      deriveAgentRunActions(
        { action: "respond", status: "execution_failed" },
        deriveAgentExperience(canFrom([CAPABILITIES.AGENT_RUN])),
      ).canExecuteRun,
      false,
    );
  });

  it("16: tool.authorized does not grant execution", () => {
    const run = {
      ...RUN_APPROVED,
      tool_plan: [
        {
          tool: "zendesk.reply",
          arguments: {},
          risk_level: "high",
          requires_approval: true,
          authorized: true,
        },
      ],
    };
    const plan = deriveAgentRunActions(
      run,
      deriveAgentExperience(canFrom([CAPABILITIES.AGENT_RUN])),
    );
    assert.equal(plan.canExecuteRun, false);
  });

  it("17: low risk does not itself grant execution", () => {
    const run = {
      ...RUN_APPROVED,
      tool_plan: [
        {
          tool: "zendesk.reply",
          arguments: {},
          risk_level: "low",
          requires_approval: false,
          authorized: false,
        },
      ],
    };
    assert.equal(
      deriveAgentRunActions(
        run,
        deriveAgentExperience(canFrom([CAPABILITIES.AGENT_RUN])),
      ).canExecuteRun,
      false,
    );
  });

  it("18: requires_approval=false does not itself grant execution permission", () => {
    const run = {
      ...RUN_APPROVED,
      tool_plan: [
        {
          tool: "zendesk.reply",
          arguments: {},
          risk_level: "low",
          requires_approval: false,
          authorized: true,
        },
      ],
    };
    assert.equal(
      deriveAgentRunActions(
        run,
        deriveAgentExperience(canFrom([CAPABILITIES.AGENT_RUN])),
      ).canExecuteRun,
      false,
    );
  });
});

describe("analyze with AI gating", () => {
  it("19: agent.run enables Analyze with AI", () => {
    assert.equal(deriveAgentExperience(canFrom([CAPABILITIES.AGENT_RUN])).canRun, true);
  });

  it("20: ticket.write without agent.run does not enable Analyze with AI", () => {
    const agent = deriveAgentExperience(
      canFrom([CAPABILITIES.TICKET_READ, CAPABILITIES.TICKET_WRITE]),
    );
    assert.equal(agent.canRun, false);
  });

  it("21: ticket.read without agent.run does not enable Analyze with AI", () => {
    const agent = deriveAgentExperience(canFrom([CAPABILITIES.TICKET_READ]));
    assert.equal(agent.canRun, false);
  });
});

describe("agent mutation error handling", () => {
  const forbidden = "Insufficient permissions";
  const membership = "Organization membership not found";

  it("22: approve capability 403 gives friendly denial, refresh, no retry", () => {
    const plan = planClientAuthorizationResponse(403, forbidden);
    assert.equal(plan.kind, "capability-denied");
    assert.equal(plan.refreshAuthorization, true);
    assert.equal(plan.recoverTenant, false);
    assert.equal(authorizationFeedback(plan), PERMISSION_DENIED_FEEDBACK);
    assert.equal("retry" in plan, false);
  });

  it("23: reject capability 403 gives friendly denial, refresh, no retry", () => {
    const plan = planClientAuthorizationResponse(403, forbidden);
    assert.equal(plan.kind, "capability-denied");
    assert.equal(plan.refreshAuthorization, true);
    assert.equal(authorizationFeedback(plan) !== null, true);
  });

  it("24: execute capability 403 gives friendly denial, refresh, no retry", () => {
    const partial = planPartialExecutionFailure(403, forbidden);
    assert.equal(partial.refreshAuthorization, true);
    assert.equal(partial.recoverTenant, false);
    assert.equal(partial.resentApproval, false);
    assert.equal(partial.retryExecute, false);
    assert.equal(partial.message, PARTIAL_EXECUTION_CAPABILITY_FEEDBACK);
  });

  it("25: analyze capability 403 gives friendly denial, refresh, no retry", () => {
    const plan = planClientAuthorizationResponse(403, forbidden);
    assert.equal(plan.kind, "capability-denied");
    assert.equal(plan.refreshAuthorization, true);
    assert.equal("retry" in plan, false);
  });

  it("26: membership failure recovers the tenant with no mutation retry", () => {
    const plan = planClientAuthorizationResponse(403, membership);
    assert.equal(plan.kind, "membership-invalid");
    assert.equal(plan.recoverTenant, true);
    assert.equal(plan.refreshAuthorization, false);
    assert.equal("retry" in plan, false);
  });
});

describe("partial success planning", () => {
  it("27: approve + execute success is a full success, not partial", () => {
    const message = approvalQueuedMessage(41);
    assert.equal(message, "Approved and queued successfully — job #41.");
    assert.equal(message.includes("could not be queued"), false);
    assert.equal(
      executionQueuedMessage(41),
      "Execution queued successfully — job #41.",
    );
  });

  it("28: approve succeeds + execute fails is partial success and never resends approval", () => {
    const partial = planPartialExecutionFailure(
      409,
      "Agent run cannot be queued from status approved.",
    );
    assert.equal(partial.kind, "partial-success");
    assert.equal(partial.resentApproval, false);
    assert.equal(partial.retryExecute, false);
    assert.equal(partial.message, PARTIAL_EXECUTION_FEEDBACK);
  });

  it("29: approve succeeds + execute capability 403 refreshes auth without re-approval", () => {
    const partial = planPartialExecutionFailure(403, "Insufficient permissions");
    assert.equal(partial.kind, "partial-success");
    assert.equal(partial.refreshAuthorization, true);
    assert.equal(partial.resentApproval, false);
    assert.equal(partial.retryExecute, false);
  });

  it("30: approve succeeds + membership failure on execute triggers tenant recovery", () => {
    const partial = planPartialExecutionFailure(
      403,
      "Organization membership not found",
    );
    assert.equal(partial.kind, "partial-success");
    assert.equal(partial.recoverTenant, true);
    assert.equal(partial.resentApproval, false);
    assert.equal(partial.retryExecute, false);
    assert.equal(partial.message, PARTIAL_EXECUTION_MEMBERSHIP_FEEDBACK);
  });
});

describe("stale authorization", () => {
  it("31: a capability downgrade after render changes behavior on refresh", () => {
    const before = deriveAgentExperience(
      canFrom([
        CAPABILITIES.AGENT_RUN,
        CAPABILITIES.AGENT_APPROVE,
        CAPABILITIES.AGENT_EXECUTE,
      ]),
    );
    const after = deriveAgentExperience(
      canFrom([CAPABILITIES.AGENT_RUN]),
    );
    assert.equal(before.canApprove, true);
    assert.equal(before.canExecute, true);
    assert.equal(after.canApprove, false);
    assert.equal(after.canExecute, false);
  });

  it("32: a role-only change does not alter Agent behavior", () => {
    const capabilities = [
      CAPABILITIES.AGENT_RUN,
      CAPABILITIES.AGENT_APPROVE,
    ];
    const before = deriveAgentExperience(
      createAuthorizationView({
        organization_id: 1,
        role: "agent",
        capabilities,
      }).can,
    );
    const after = deriveAgentExperience(
      createAuthorizationView({
        organization_id: 1,
        role: "owner",
        capabilities,
      }).can,
    );
    assert.deepEqual(before, after);
    assert.equal(after.canExecute, false);
  });
});
