import {
  neonStagingEnabled, stagingSubjects, STAGING_BACKEND_URL,
} from "./neon-policy.ts";

export type TicketActionOutcome = "blocked" | "unavailable" | "conflict" | "completed";

type Identity = { user: { id: string }; token: string } | null;
type Dependencies = {
  env: Readonly<Record<string, string | undefined>>;
  form: FormData;
  readIdentity: (allowedSubjects: readonly string[]) => Promise<Identity>;
  fetcher: typeof fetch;
};

/** Fully testable policy/execution core for the server-only form action. */
export async function runSyntheticTicketAction({
  env, form, readIdentity, fetcher,
}: Dependencies): Promise<TicketActionOutcome> {
  const enabled = neonStagingEnabled(env) &&
    env.CXOPS_STAGING_TICKET_ACTIONS_ENABLED === "true";
  const allowedSubject = env.CXOPS_STAGING_TICKET_AGENT_SUBJECT ?? "";
  const idRaw = String(form.get("ticket_id") ?? "");
  const action = String(form.get("operation") ?? "");
  const expectedVersion = String(form.get("expected_version") ?? "");
  if (
    !enabled || !allowedSubject || !/^\d{1,10}$/.test(idRaw) ||
    Number(idRaw) < 1 || Number(idRaw) > 2147483647 ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(expectedVersion) ||
    !["claim", "release", "resolve"].includes(action)
  ) return "blocked";

  try {
    const identity = await readIdentity(stagingSubjects(env));
    if (identity?.user.id !== allowedSubject) return "blocked";
    const memberships = await fetcher(STAGING_BACKEND_URL + "/me/organizations", {
      headers: { Authorization: `Bearer ${identity.token}` },
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
    });
    if (!memberships.ok) return "unavailable";
    const organizations: unknown = await memberships.json();
    if (!Array.isArray(organizations) || organizations.length !== 1) return "blocked";
    const organization = organizations[0] as Record<string, unknown>;
    if (!Number.isSafeInteger(organization.id) || Number(organization.id) <= 0 ||
        organization.name !== "Synthetic Alpha Workshop") return "blocked";
    const organizationId = Number(organization.id);
    const headers = {
      Authorization: `Bearer ${identity.token}`,
      "X-CXOps-Organization-ID": String(organizationId),
      Accept: "application/json",
    };
    const authorized = await fetcher(STAGING_BACKEND_URL + "/me/authorization", {
      headers, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
    });
    if (!authorized.ok) return "unavailable";
    const value: unknown = await authorized.json();
    if (!value || typeof value !== "object" ||
        !("role" in value) || value.role !== "agent" ||
        !("organization_id" in value) || value.organization_id !== organizationId ||
        !("capabilities" in value) || !Array.isArray(value.capabilities) ||
        !value.capabilities.includes("ticket.write")) return "blocked";
    const response = await fetcher(STAGING_BACKEND_URL + "/staging/ticket-action", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        ticket_id: Number(idRaw), action, expected_version: expectedVersion,
      }),
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
    });
    return response.ok ? "completed" : response.status === 409 ? "conflict" : "unavailable";
  } catch {
    return "unavailable";
  }
}
