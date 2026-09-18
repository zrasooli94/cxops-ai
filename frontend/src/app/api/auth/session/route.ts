import { NextRequest } from "next/server";
import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { createNhostServerClient } from "@/lib/nhost/server";
import { performSessionRecovery } from "@/lib/session/recovery-helpers";
import { sanitizeNextPath } from "@/lib/session/read";
import { SESSION_COOKIE_NAME } from "@/lib/session/helpers";
import { ACTIVE_ORGANIZATION_COOKIE_NAME } from "@/lib/tenant/cookie-helpers";

/**
 * Session recovery endpoint (Route Handler = MUTABLE context, cookie writes
 * allowed). Server Components must never call this logic inlined; they only
 * `redirect()` here when the classified session is stale.
 *
 * Exactly one refresh attempt. Outcome:
 * - no usable session            -> GET /login
 * - valid session                -> GET <safe next or /dashboard>
 * - stale, refresh succeeded     -> GET <safe next or /dashboard>
 * - stale, refresh failed/threw  -> clear session + org cookies, GET /login
 *
 * `next` is sanitized to a same-origin path; external values fall back to
 * /dashboard.
 */
export async function GET(request: NextRequest) {
  const safeNext = sanitizeNextPath(
    new URL(request.url).searchParams.get("next"),
  );

  const nhost = await createNhostServerClient();
  const cookieStore = await cookies();

  const result = await performSessionRecovery({
    readSession: () => nhost.getUserSession(),
    nowSeconds: () => Math.floor(Date.now() / 1000),
    refresh: (marginSeconds) => nhost.refreshSession(marginSeconds),
    clearSession: () => nhost.clearSession(),
    deleteSessionCookie: () => cookieStore.delete(SESSION_COOKIE_NAME),
    deleteOrganizationCookie: () =>
      cookieStore.delete(ACTIVE_ORGANIZATION_COOKIE_NAME),
    safeNext,
  });

  return redirect(result.destination);
}