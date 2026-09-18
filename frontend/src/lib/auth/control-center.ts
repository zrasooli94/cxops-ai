"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import {
  SESSION_COOKIE_NAME,
  deserializeSession,
} from "@/lib/session/helpers";
import {
  classifySessionState,
  protectedDecision,
  type SessionState,
} from "@/lib/session/read";

export interface AuthenticatedSession {
  displayName: string | null;
  email: string | null;
}

export interface ControlCenterSessionState {
  state: SessionState;
  session: AuthenticatedSession | null;
}

/**
 * READ-ONLY session access for Server Components.
 *
 * This never constructs an Nhost client, never refreshes, and never writes or
 * deletes cookies: mutating a cookie during Server Component rendering is not
 * supported by Next.js and crashes the render. All mutation is deferred to a
 * Server Function or a Route Handler (see `app/api/auth/session/route.ts`).
 */
export async function readControlCenterSession(): Promise<ControlCenterSessionState> {
  const cookieStore = await cookies();
  const raw = cookieStore.get(SESSION_COOKIE_NAME)?.value ?? null;
  const parsed = deserializeSession(raw);
  const state = classifySessionState(parsed);

  const session = parsed?.user
    ? {
        displayName: parsed.user.displayName ?? null,
        email: parsed.user.email ?? null,
      }
    : null;

  return { state, session };
}

export async function getControlCenterSession(): Promise<AuthenticatedSession | null> {
  const { session } = await readControlCenterSession();
  return session;
}

export async function shouldRecoverSession(): Promise<boolean> {
  const { state } = await readControlCenterSession();
  return state === "stale";
}

export async function requireControlCenterAuth(): Promise<AuthenticatedSession> {
  const { state, session } = await readControlCenterSession();
  const decision = protectedDecision(state);

  if (decision === "login") {
    redirect("/login");
  }
  if (decision === "recover") {
    // A stale (expired) cookie must be refreshed through a MUTABLE context
    // (Route Handler) - never during Server Component rendering.
    redirect("/api/auth/session");
  }

  return session as AuthenticatedSession;
}