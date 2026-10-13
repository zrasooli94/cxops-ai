// Browser client for the Phase 1Q staff surfaces.
//
// Every call goes through the authenticated control-center proxy
// (/api/backend/...), which attaches the session and the active-organization
// selection. The backend is the authorization boundary; nothing here asserts a
// tenant, a role or an operator grant.

export class CommercialApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function readError(response: Response): Promise<CommercialApiError> {
  let detail = "Something went wrong. Please try again.";
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const value = (body as { detail: unknown }).detail;
      if (typeof value === "string") {
        detail = value;
      } else if (Array.isArray(value) && value[0] && typeof value[0].msg === "string") {
        detail = value[0].msg;
      }
    }
  } catch {
    // Non-JSON error bodies keep the generic message.
  }
  if (response.status === 403 && detail === "Insufficient permissions") {
    detail = "Your role does not allow this action.";
  }
  return new CommercialApiError(response.status, detail);
}

export async function requestJson<T>(
  path: string,
  init: { method?: string; body?: unknown } = {},
  fetcher: typeof fetch = fetch,
): Promise<T> {
  const response = await fetcher(`/api/backend${path}`, {
    method: init.method ?? "GET",
    cache: "no-store",
    headers: init.body === undefined ? undefined : { "Content-Type": "application/json" },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (!response.ok) {
    throw await readError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

// ---------------------------------------------------------------- types --

export interface OnboardingState {
  organization: {
    id: number;
    name: string;
    industry: string | null;
    website_url: string | null;
    lifecycle_status: string;
    plan_code: string | null;
    submitted_for_review_at: string | null;
  };
  steps: { key: string; title: string; required: boolean; complete: boolean }[];
  missing_required: string[];
  can_submit: boolean;
  can_edit: boolean;
  warnings: string[];
  widget: null | {
    display_name: string;
    welcome_message: string;
    theme_token: string | null;
    allowed_origins: string[];
    enabled: boolean;
    has_widget_key: boolean;
    last_session_at: string | null;
  };
  knowledge: { documents: number; chunks: number; upload_available: boolean };
  team: { members: number; pending_invitations: number };
  theme_options: string[];
}

export type Unavailable = { available: false; reason: string };

export interface WorkspaceOverview {
  organization: {
    id: number;
    name: string;
    industry: string | null;
    website_url: string | null;
    lifecycle_status: string;
    plan_code: string | null;
  };
  your_role: string;
  chatbot:
    | Unavailable
    | {
        available: true;
        configured: boolean;
        display_name: string | null;
        enabled: boolean;
        live: boolean;
        allowed_origins: string[];
        has_widget_key: boolean;
        installation: { detected: boolean; last_session_at: string | null };
      };
  knowledge:
    | Unavailable
    | { available: true; documents: number; last_added_at: string | null; ready: boolean };
  conversations:
    | Unavailable
    | {
        available: true;
        last_7_days: number;
        recent: { session_id: number; status: string; started_at: string | null; assigned: boolean }[];
      };
  handoff: Unavailable | { available: true; waiting: number; assigned: number };
  usage:
    | Unavailable
    | {
        available: true;
        period_start: string;
        turns_used: number;
        turn_limit: number;
        remaining_turns: number;
        estimated_cost_usd: number;
        budget_usd: number;
        remaining_budget_usd: number;
        limit_reached: boolean;
        limit_reason: string | null;
        cost_basis: "estimated";
        chat_turns_24h: {
          completed: number;
          failed: number;
          open: number;
          p50_seconds: number | null;
          p95_seconds: number | null;
        };
      };
  team: Unavailable | { available: true; members: number; pending_invitations: number };
  actions: string[];
}

export interface TeamMember {
  id: number;
  email: string | null;
  role: string;
  is_you: boolean;
  joined_at: string | null;
  can_manage: boolean;
}

export interface TeamInvitation {
  id: number;
  email: string;
  role: string;
  status: "pending" | "expired" | "accepted" | "revoked";
  expires_at: string;
  created_at: string | null;
  token?: string;
}

// ------------------------------------------------------------ endpoints --

export const commercialApi = {
  onboardingState: () => requestJson<OnboardingState>("/onboarding/state"),
  updateBusiness: (body: { name?: string; industry?: string | null; website_url?: string | null }) =>
    requestJson<OnboardingState>("/onboarding/business", { method: "PATCH", body }),
  createWidget: (body: {
    display_name: string;
    welcome_message: string;
    theme_token: string | null;
    allowed_origins: string[];
  }) =>
    requestJson<{ public_widget_key: string; warning: string }>("/onboarding/widget", {
      method: "POST",
      body,
    }),
  updateWidget: (body: Record<string, unknown>) =>
    requestJson<OnboardingState>("/onboarding/widget", { method: "PATCH", body }),
  submitForReview: () => requestJson<OnboardingState>("/onboarding/submit", { method: "POST" }),

  workspace: () => requestJson<WorkspaceOverview>("/workspace/overview"),

  members: () =>
    requestJson<{
      members: TeamMember[];
      can_manage: boolean;
      assignable_roles: string[];
      your_role: string;
    }>("/team/members"),
  changeRole: (membershipId: number, role: string) =>
    requestJson<{ id: number; role: string }>(`/team/members/${membershipId}`, {
      method: "PATCH",
      body: { role },
    }),
  removeMember: (membershipId: number) =>
    requestJson<void>(`/team/members/${membershipId}`, { method: "DELETE" }),
  invitations: () => requestJson<{ invitations: TeamInvitation[] }>("/team/invitations"),
  invite: (email: string, role: string) =>
    requestJson<TeamInvitation & { token: string; delivery: string }>("/team/invitations", {
      method: "POST",
      body: { email, role },
    }),
  revokeInvitation: (id: number) =>
    requestJson<TeamInvitation>(`/team/invitations/${id}/revoke`, { method: "POST" }),

  previewInvitation: (token: string) =>
    requestJson<{ organization_name: string; role: string; email: string; expires_at: string }>(
      "/invitations/preview",
      { method: "POST", body: { token } },
    ),
  acceptInvitation: (token: string) =>
    requestJson<{ organization_id: number; organization_name: string; role: string }>(
      "/invitations/accept",
      { method: "POST", body: { token } },
    ),
};
