import type { StoredSession } from "@nhost/nhost-js/session";
import { classifySessionState, type SessionState } from "./read.ts";

export type SessionRecoveryOutcome =
  | "missing"
  | "valid"
  | "recovered"
  | "cleared";

export interface SessionRecoveryResult {
  outcome: SessionRecoveryOutcome;
  destination: string;
}

export interface SessionRecoveryDeps {
  readSession: () => StoredSession | null;
  nowSeconds: () => number;
  refresh: (marginSeconds: number) => Promise<StoredSession | null>;
  clearSession: () => void;
  deleteSessionCookie: () => void;
  deleteOrganizationCookie: () => void;
  safeNext?: string | null;
}

const DEFAULT_DESTINATION = "/dashboard";
const LOGIN_DESTINATION = "/login";

/**
 * Recover a session exactly once. Used by the `/api/auth/session` Route
 * Handler, an explicitly MUTABLE context (cookie writes are allowed there).
 *
 * - no usable session -> go to login, never refresh
 * - valid session -> go to the safe destination, never refresh
 * - stale session -> refresh exactly once; on success go to the safe
 *   destination; on any failure clear the session cookie AND the active
 *   organization cookie, then go to login (never loops, never redirects to an
 *   unsanitized external `next`).
 */
export async function performSessionRecovery(
  deps: SessionRecoveryDeps,
): Promise<SessionRecoveryResult> {
  const state: SessionState = classifySessionState(
    deps.readSession(),
    deps.nowSeconds(),
  );

  const safeDestination = deps.safeNext ?? DEFAULT_DESTINATION;

  if (state === "missing") {
    return { outcome: "missing", destination: LOGIN_DESTINATION };
  }

  if (state === "valid") {
    return { outcome: "valid", destination: safeDestination };
  }

  let refreshed: StoredSession | null = null;
  try {
    refreshed = await deps.refresh(0);
  } catch {
    refreshed = null;
  }

  if (refreshed?.accessToken) {
    return { outcome: "recovered", destination: safeDestination };
  }

  try {
    deps.clearSession();
  } catch {
    // Best-effort: the cookie cleanup below is authoritative.
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

  return { outcome: "cleared", destination: LOGIN_DESTINATION };
}