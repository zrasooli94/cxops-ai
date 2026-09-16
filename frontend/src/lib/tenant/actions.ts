"use server";

import { redirect } from "next/navigation";
import {
  setActiveOrganizationId,
  clearActiveOrganizationId,
} from "./cookie";
import {
  getMyOrganizations,
  validateOrganizationSelection,
  TenantSelectionError,
} from "./backend-client";

export interface SelectOrganizationResult {
  ok: boolean;
  error?: string;
}

/**
 * Validate the requested organization through FastAPI and, only on success,
 * persist it as the active-organization UX cookie.
 *
 * Forged or non-member selections leave the existing cookie unchanged.
 */
export async function selectOrganization(
  organizationId: number,
): Promise<SelectOrganizationResult> {
  const parsed = Number(organizationId);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return { ok: false, error: "Invalid organization selection" };
  }

  try {
    await validateOrganizationSelection(parsed);
  } catch (error) {
    if (error instanceof TenantSelectionError) {
      return {
        ok: false,
        error:
          error.status === 401
            ? "Session expired. Please sign in again."
            : "Organization membership not found.",
      };
    }
    return { ok: false, error: "Could not validate selection" };
  }

  await setActiveOrganizationId(parsed);
  redirect("/dashboard");
}

/**
 * Switch to a different organization after validating membership.
 *
 * Always lands on the dashboard so tenant-owned page state from the previous
 * organization is not preserved.
 */
export async function switchOrganization(
  organizationId: number,
): Promise<SelectOrganizationResult> {
  return selectOrganization(organizationId);
}

/**
 * Clear a stale or invalid active-organization cookie and require reselection.
 */
export async function clearOrganizationSelection(): Promise<void> {
  await clearActiveOrganizationId();
}

export { getMyOrganizations };
