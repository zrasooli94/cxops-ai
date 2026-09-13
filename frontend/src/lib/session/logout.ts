"use server";

import { cookies } from "next/headers";
import { createNhostServerClient } from "@/lib/nhost/server";
import { performLogout, type LogoutDeps, type LogoutResult } from "./logout-helpers";

export async function logout(): Promise<LogoutResult> {
  const nhost = await createNhostServerClient();
  const cookieStore = await cookies();
  const COOKIE_NAME = process.env.NHOST_SESSION_COOKIE ?? "nhostSession";

  const deps: LogoutDeps = {
    getSession: () => nhost.getUserSession(),
    signOut: async (refreshToken: string) => {
      const result = await nhost.auth.signOut({ refreshToken });
      return { status: result.status, body: result.body };
    },
    clearSession: () => nhost.clearSession(),
    deleteCookie: () => cookieStore.delete(COOKIE_NAME),
  };

  return performLogout(deps);
}