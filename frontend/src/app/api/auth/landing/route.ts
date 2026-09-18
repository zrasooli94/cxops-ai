import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  landingDestination,
  landingMembershipLoadDisposition,
} from "@/lib/session/read";
import { SESSION_COOKIE_NAME } from "@/lib/session/helpers";
import { readSession } from "@/lib/session/read";
import { getMyOrganizations } from "@/lib/tenant/backend-client";
import {
  getActiveOrganizationId,
  setActiveOrganizationId,
} from "@/lib/tenant/cookie";

/**
 * Post-login landing endpoint (Route Handler = MUTABLE context). Owns the
 * destination decision that used to run during Server Component rendering:
 * memberships are loaded here and the active-organization cookie is persisted
 * here, never inside a Server Component.
 *
 * Server Components redirect here when the session is valid; this handler
 * redirects through /api/auth/session when the session is stale, so exactly
 * one recovery happens before membership resolution. When the membership load
 * itself fails because authentication broke, the handler ALSO redirects
 * through /api/auth/session (never a 500); any other backend failure answers
 * with a generic 503 and no body so no internal detail or token leaks.
 */
export async function GET() {
  const cookieStore = await cookies();
  const state = readSession(
    () => cookieStore.get(SESSION_COOKIE_NAME)?.value ?? null,
  );

  if (state === "missing") {
    return redirect("/login");
  }
  if (state === "stale") {
    return redirect("/api/auth/session");
  }

  let memberships;
  try {
    memberships = await getMyOrganizations();
  } catch (error) {
    if (landingMembershipLoadDisposition(error) === "recover") {
      return redirect("/api/auth/session");
    }
    return new NextResponse(null, { status: 503 });
  }
  const activeOrganizationId = await getActiveOrganizationId();

  if (memberships.length === 0) {
    return redirect("/no-organization");
  }

  if (memberships.length === 1) {
    await setActiveOrganizationId(memberships[0].id);
    return redirect("/dashboard");
  }

  const destination = landingDestination(memberships, activeOrganizationId);
  return redirect(destination);
}