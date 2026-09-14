"use server";

import { redirect } from "next/navigation";
import { createNhostServerClient } from "@/lib/nhost/server";

export interface AuthenticatedSession {
  displayName: string | null;
  email: string | null;
}

export async function getControlCenterSession(): Promise<AuthenticatedSession | null> {
  try {
    const nhost = await createNhostServerClient();
    const session = nhost.getUserSession();

    if (!session || !session.user) {
      return null;
    }

    return {
      displayName: session.user.displayName ?? null,
      email: session.user.email ?? null,
    };
  } catch {
    return null;
  }
}

export async function requireControlCenterAuth(): Promise<AuthenticatedSession> {
  const session = await getControlCenterSession();

  if (!session) {
    redirect("/login");
  }

  return session;
}