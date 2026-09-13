import type { StoredSession } from "@nhost/nhost-js/session";

export const SESSION_COOKIE_NAME = process.env.NHOST_SESSION_COOKIE ?? "nhostSession";

export function serializeSession(session: StoredSession): string {
  return JSON.stringify(session);
}

export function deserializeSession(raw: string | null): StoredSession | null {
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof parsed.accessToken === "string" &&
      typeof parsed.refreshToken === "string" &&
      typeof parsed.user === "object"
    ) {
      return parsed as StoredSession;
    }
    return null;
  } catch {
    return null;
  }
}

export function getAccessToken(session: StoredSession | null): string | null {
  return session?.accessToken ?? null;
}

export function hasValidSession(session: StoredSession | null): boolean {
  if (!session) return false;
  if (!session.accessToken) return false;
  if (!session.refreshToken) return false;
  if (!session.user) return false;
  return true;
}