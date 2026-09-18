import type { StoredSession } from "@nhost/nhost-js/session";

export interface BffRefreshResult {
  authHeader: string | null;
  cleared: boolean;
}

export interface BffRefreshDeps {
  getSession: () => StoredSession | null;
  refresh: (marginSeconds: number) => Promise<StoredSession | null>;
  clearSession: () => void;
  deleteSessionCookie: () => void;
  deleteOrganizationCookie: () => void;
}

/**
 * Produce an authorization header by refreshing the session exactly once.
 *
 * - no access token -> no header, nothing cleared
 * - refresh failed or produced no new token -> clear the session AND the
 *   active-organization cookie, return no header (no retry loop here; the
 *   caller may issue exactly one forced retry through a further call)
 * - refresh succeeded -> `Bearer <token>`
 */
export async function refreshSessionOnce(
  deps: BffRefreshDeps,
  marginSeconds = 60,
): Promise<BffRefreshResult> {
  const session = deps.getSession();
  if (!session?.accessToken) {
    return { authHeader: null, cleared: false };
  }

  let refreshed: StoredSession | null = null;
  try {
    refreshed = await deps.refresh(marginSeconds);
  } catch {
    refreshed = null;
  }

  if (!refreshed?.accessToken) {
    try {
      deps.clearSession();
    } catch {
      // Best-effort.
    }
    try {
      deps.deleteSessionCookie();
    } catch {
      // Best-effort.
    }
    try {
      deps.deleteOrganizationCookie();
    } catch {
      // Best-effort.
    }
    return { authHeader: null, cleared: true };
  }

  return { authHeader: `Bearer ${refreshed.accessToken}`, cleared: false };
}