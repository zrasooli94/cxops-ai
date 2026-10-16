"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { parseStagingOrganization } from "@/lib/staging/operations-contract";
import {
  neonStagingEnabled, stagingIdentity, stagingSubjects, STAGING_BACKEND_URL,
} from "@/lib/staging/neon-policy";
import { stagingAuth } from "@/lib/staging/neon-server";

type Outcome = "blocked" | "unavailable" | "conflict" | "completed";

/** Server-only form action. Never accept an arbitrary tenant, URL or bearer token. */
export async function performSyntheticTicketAction(form: FormData): Promise<void> {
  let outcome: Outcome = "blocked";
  const enabled = neonStagingEnabled(process.env) &&
    process.env.CXOPS_STAGING_TICKET_ACTIONS_ENABLED === "true";
  const allowedSubject = process.env.CXOPS_STAGING_TICKET_AGENT_SUBJECT ?? "";
  const idRaw = String(form.get("ticket_id") ?? "");
  const action = String(form.get("operation") ?? "");

  if (
    enabled &&
    allowedSubject &&
    /^\d{1,10}$/.test(idRaw) &&
    Number(idRaw) >= 1 &&
    Number(idRaw) <= 2147483647 &&
    ["claim", "release", "resolve"].includes(action)
  ) {
    try {
      const identity = await stagingIdentity(stagingAuth(), stagingSubjects(process.env));
      if (identity?.user.id === allowedSubject) {
        const memberships = await fetch(STAGING_BACKEND_URL + "/me/organizations", {
          headers: { Authorization: `Bearer ${identity.token}` },
          cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
        });
        if (memberships.ok) {
          const organization = parseStagingOrganization(await memberships.json() as unknown);
          if (organization.name === "Synthetic Alpha Workshop") {
            const headers = {
              Authorization: `Bearer ${identity.token}`,
              "X-CXOps-Organization-ID": String(organization.id),
              Accept: "application/json",
            };
            const authorized = await fetch(STAGING_BACKEND_URL + "/me/authorization", {
              headers, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
            });
            if (authorized.ok) {
              const value: unknown = await authorized.json();
              if (
                value && typeof value === "object" &&
                "role" in value && value.role === "agent" &&
                "organization_id" in value && value.organization_id === organization.id &&
                "capabilities" in value && Array.isArray(value.capabilities) &&
                value.capabilities.includes("ticket.write")
              ) {
                const response = await fetch(STAGING_BACKEND_URL + "/staging/ticket-action", {
                  method: "POST",
                  headers: { ...headers, "Content-Type": "application/json" },
                  body: JSON.stringify({ ticket_id: Number(idRaw), action }),
                  cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
                });
                outcome = response.ok ? "completed" : response.status === 409 ? "conflict" : "unavailable";
              }
            }
          }
        }
      }
    } catch {
      // Do not report identity, token, full URL, provider output or payload.
      outcome = "unavailable";
    }
  }
  if (outcome === "completed") revalidatePath("/staging/operations");
  redirect(outcome === "completed" ? "/staging/operations" : `/staging/operations?workflow=${outcome}`);
}
