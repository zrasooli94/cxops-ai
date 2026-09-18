/**
 * Pure Agent authorization UX derivation.
 *
 * Frontend UX only. FastAPI remains the authoritative security boundary: the
 * execute endpoint independently re-validates run state, durable human
 * approval, intent digest, tool policy and tool-required capabilities on every
 * request. Nothing in this module grants anything.
 *
 * Every decision uses backend-returned capability strings only. Role, tool plan
 * fields (`authorized`, `risk_level`, `requires_approval`) and model output
 * never participate. In particular this module never inspects the tool plan to
 * decide whether execution is permitted.
 */
import { CAPABILITIES, type Capability } from "./capabilities.ts";
import { classifyClientAuthorizationUx } from "./helpers.ts";

/**
 * Agent screen shape derived from capabilities only.
 *
 * The three capabilities are independent: `agent.approve` does not imply
 * `agent.execute` and `agent.execute` does not imply `agent.approve`.
 */
export interface AgentExperience {
  readonly canRun: boolean;
  readonly canApprove: boolean;
  readonly canExecute: boolean;
}

export function deriveAgentExperience(
  can: (capability: Capability) => boolean,
): AgentExperience {
  return {
    canRun: can(CAPABILITIES.AGENT_RUN),
    canApprove: can(CAPABILITIES.AGENT_APPROVE),
    canExecute: can(CAPABILITIES.AGENT_EXECUTE),
  };
}

/**
 * Run statuses the backend execute endpoint accepts, mirrored from
 * `app/api/routes/agent.py`. Used only for UX eligibility; the backend stays
 * authoritative and may still refuse for other reasons.
 */
export const EXECUTABLE_RUN_STATUSES: readonly string[] = [
  "approved",
  "execution_failed",
];

/** Actions the backend execute endpoint refuses to execute externally. */
export const NON_EXECUTABLE_RUN_ACTIONS: readonly string[] = [
  "human_review",
  "no_action",
];

/** Structural run fields needed to derive action availability. */
export interface AgentRunShape {
  readonly action: string;
  readonly status: string;
}

/** Whether the backend execute endpoint accepts this run's action. */
export function isRunExternallyExecutable(run: {
  action: string;
}): boolean {
  return !NON_EXECUTABLE_RUN_ACTIONS.includes(run.action);
}

/** Whether the backend execute endpoint accepts this run's current status. */
export function isRunExecutableStatus(status: string): boolean {
  return EXECUTABLE_RUN_STATUSES.includes(status);
}

/**
 * UI possibilities for a single run. This must never be read as the backend
 * security policy: `canExecuteRun` means "the browser may offer the action",
 * not "execution will succeed".
 */
export interface AgentRunActionPlan {
  readonly canApproveRun: boolean;
  readonly canRejectRun: boolean;
  /** Approving should also attempt to queue external execution. */
  readonly canApproveAndExecute: boolean;
  /** An independent execution/retry control may be offered for this run. */
  readonly canExecuteRun: boolean;
}

export function deriveAgentRunActions(
  run: AgentRunShape,
  agent: AgentExperience,
): AgentRunActionPlan {
  const externallyExecutable = isRunExternallyExecutable(run);
  const executableNow =
    externallyExecutable && isRunExecutableStatus(run.status);

  return {
    canApproveRun: agent.canApprove,
    canRejectRun: agent.canApprove,
    canApproveAndExecute:
      agent.canApprove && agent.canExecute && externallyExecutable,
    canExecuteRun: agent.canExecute && executableNow,
  };
}

export const PARTIAL_EXECUTION_FEEDBACK =
  "Run approved, but execution could not be queued.";
export const PARTIAL_EXECUTION_CAPABILITY_FEEDBACK =
  "Run approved. You no longer have permission to execute agent runs.";
export const PARTIAL_EXECUTION_MEMBERSHIP_FEEDBACK =
  "Run approved, but your organization access changed. Refreshing the workspace…";

/**
 * UX result for the specific case where approval already succeeded and the
 * subsequent queue request failed.
 *
 * The literals `resentApproval: false` and `retryExecute: false` are part of
 * the contract: approval is never re-sent and execution is never retried
 * automatically. The approval outcome is never relabelled as a failure.
 */
export interface PartialExecutionUx {
  readonly kind: "partial-success";
  readonly refreshAuthorization: boolean;
  readonly recoverTenant: boolean;
  readonly message: string;
  readonly resentApproval: false;
  readonly retryExecute: false;
}

export function planPartialExecutionFailure(
  status: number,
  detail: unknown,
): PartialExecutionUx {
  const ux = classifyClientAuthorizationUx(status, detail);

  if (ux === "capability-denied") {
    return {
      kind: "partial-success",
      refreshAuthorization: true,
      recoverTenant: false,
      message: PARTIAL_EXECUTION_CAPABILITY_FEEDBACK,
      resentApproval: false,
      retryExecute: false,
    };
  }

  if (ux === "membership-invalid") {
    return {
      kind: "partial-success",
      refreshAuthorization: false,
      recoverTenant: true,
      message: PARTIAL_EXECUTION_MEMBERSHIP_FEEDBACK,
      resentApproval: false,
      retryExecute: false,
    };
  }

  return {
    kind: "partial-success",
    refreshAuthorization: false,
    recoverTenant: false,
    message: PARTIAL_EXECUTION_FEEDBACK,
    resentApproval: false,
    retryExecute: false,
  };
}

function withJobSuffix(base: string, jobId: unknown): string {
  const suffix =
    typeof jobId === "number" || typeof jobId === "string"
      ? ` — job #${jobId}`
      : "";
  return `${base}${suffix}.`;
}

/** Friendly success message for an approve-then-queue operation. */
export function approvalQueuedMessage(jobId: unknown): string {
  return withJobSuffix("Approved and queued successfully", jobId);
}

/** Friendly success message for an independent execution/retry. */
export function executionQueuedMessage(jobId: unknown): string {
  return withJobSuffix("Execution queued successfully", jobId);
}
