"use server";

import { cookies } from "next/headers";

import {
  ACTIVE_ORGANIZATION_COOKIE_NAME as COOKIE_NAME,
  parseActiveOrganizationId,
} from "./cookie-helpers";

const ONE_MONTH_SECONDS = 60 * 60 * 24 * 30;

function isProduction(): boolean {
  return process.env.NODE_ENV === "production";
}

/**
 * Return the server-managed active-organization id, or null when none is set
 * or the cookie value is invalid.
 *
 * This is UX state, not authorization. FastAPI still validates the selector
 * against the authenticated subject's organization memberships on every
 * tenant-aware request.
 */
export async function getActiveOrganizationId(): Promise<number | null> {
  const cookieStore = await cookies();
  const raw = cookieStore.get(COOKIE_NAME)?.value;
  return parseActiveOrganizationId(raw);
}

/**
 * Persist the selected organization id in a server-managed HttpOnly cookie.
 *
 * The value is validated as a positive integer before being stored. The cookie
 * carries no token, role, or organization secret data.
 */
export async function setActiveOrganizationId(
  organizationId: number,
): Promise<void> {
  if (!Number.isInteger(organizationId) || organizationId <= 0) {
    throw new Error("Invalid organization id");
  }
  const cookieStore = await cookies();
  cookieStore.set({
    name: COOKIE_NAME,
    value: String(organizationId),
    httpOnly: true,
    sameSite: "lax",
    secure: isProduction(),
    path: "/",
    maxAge: ONE_MONTH_SECONDS,
  });
}

/**
 * Remove the active-organization cookie.
 */
export async function clearActiveOrganizationId(): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.delete(COOKIE_NAME);
}
