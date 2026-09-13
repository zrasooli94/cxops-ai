import type { StoredSession } from "@nhost/nhost-js/session";

export interface LogoutResult {
  localCleared: boolean;
  remoteRevoked: boolean;
  error?: string;
}

export interface LogoutDeps {
  getSession: () => StoredSession | null;
  signOut: (refreshToken: string) => Promise<{ status: number; body: string }>;
  clearSession: () => void;
  deleteCookie: () => void;
}

export async function performLogout(deps: LogoutDeps): Promise<LogoutResult> {
  let localCleared = false;
  let remoteRevoked = false;
  let error: string | undefined;

  try {
    const session = deps.getSession();

    if (session?.refreshToken) {
      try {
        const result = await deps.signOut(session.refreshToken);
        if (result.status >= 400 || result.body !== "OK") {
          throw new Error("Remote signOut failed");
        }
        remoteRevoked = true;
      } catch {
        error = "Remote session revocation could not be confirmed";
      }
    } else if (session) {
      error = "Remote session revocation not performed: session missing refresh token";
    }

    deps.clearSession();
    localCleared = true;
    try {
      deps.deleteCookie();
    } catch {
      // Cookie deletion failed but session cleared
      error = error ? `${error}; cookie cleanup failed` : "Cookie cleanup failed";
    }
  } catch (err) {
    // Primary flow failed - try to clean up cookie
    try {
      deps.deleteCookie();
      localCleared = true;
    } catch {
      localCleared = false;
    }
    if (err instanceof Error) {
      error = err.message;
    } else {
      error = "Logout failed";
    }
  }

  return { localCleared, remoteRevoked, error };
}