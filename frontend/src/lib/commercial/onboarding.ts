// Setup wizard and workspace view models (Phase 1Q).
//
// The backend computes progress from real configuration (organization, widget,
// knowledge, team); these helpers only decide presentation.

export type LifecycleStatus = "onboarding" | "pending_review" | "active" | "suspended";

export const LIFECYCLE_LABELS: Record<string, { label: string; tone: "info" | "warn" | "ok" | "bad"; detail: string }> = {
  onboarding: {
    label: "Setting up",
    tone: "info",
    detail: "Finish the setup steps, then submit your workspace for review.",
  },
  pending_review: {
    label: "Awaiting approval",
    tone: "warn",
    detail: "CXOps is reviewing your setup. AI replies stay off until approval.",
  },
  active: {
    label: "Approved",
    tone: "ok",
    detail: "Your workspace is approved. You control when the chatbot is live.",
  },
  suspended: {
    label: "Suspended",
    tone: "bad",
    detail: "This workspace is suspended. The chatbot does not answer visitors.",
  },
};

export function lifecycleInfo(status: string | null | undefined) {
  return LIFECYCLE_LABELS[status ?? ""] ?? {
    label: "Unknown",
    tone: "bad" as const,
    detail: "The workspace status could not be determined.",
  };
}

export interface WizardStep {
  key: string;
  title: string;
  required: boolean;
  complete: boolean;
}

export const STEP_DESCRIPTIONS: Record<string, string> = {
  business: "Your business name, industry and website.",
  workspace: "Who on your team should have access.",
  domain: "The exact website addresses allowed to show the chatbot.",
  chatbot: "Assistant name, greeting and colour theme.",
  knowledge: "The answers your assistant may draw on.",
  install: "Add one line of code to your website.",
  preview: "See what visitors will see.",
  review: "Send your workspace to CXOps for approval.",
};

/** The first incomplete step (required first), or the last step when done. */
export function firstOpenStep(steps: readonly WizardStep[]): string {
  const required = steps.find((step) => step.required && !step.complete && step.key !== "review");
  if (required) {
    return required.key;
  }
  const optional = steps.find((step) => !step.complete);
  return optional ? optional.key : steps[steps.length - 1]?.key ?? "business";
}

export function progressPercent(steps: readonly WizardStep[]): number {
  if (steps.length === 0) {
    return 0;
  }
  const done = steps.filter((step) => step.complete).length;
  return Math.round((done / steps.length) * 100);
}

/** Usage bar: share of a limit used, clamped to 0-100. */
export function usageShare(used: number, limit: number): number {
  if (!Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) {
    return used > 0 ? 100 : 0;
  }
  return Math.max(0, Math.min(100, Math.round((used / limit) * 100)));
}

export const LIMIT_REASON_TEXT: Record<string, string> = {
  turn_limit_reached:
    "This month's AI conversation allowance is used up. New visitor messages go straight to your team.",
  budget_reached:
    "This month's estimated AI spending limit is reached. New visitor messages go straight to your team.",
  organization_not_active:
    "AI replies are off because this workspace is not approved or is suspended. Your team receives every message.",
  platform_budget_reached:
    "AI replies are paused platform-wide for the rest of the month. Your team still receives every message.",
};

export function formatUsd(value: number): string {
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}
