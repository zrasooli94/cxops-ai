/**
 * Post-sign-in result and navigation decision, shared by the Server Action and
 * the login form.
 *
 * This module is deliberately pure: it is imported by a `"use server"` module
 * AND by a Client Component, so it must carry no Next.js request APIs, no Node
 * globals, and nothing that would leak browser-only code into the server bundle
 * or vice versa. It is unit-tested with `node --test`.
 *
 * The destination is a compile-time constant. The decision helper never yields
 * anything but that constant, no matter what value is passed in, so the client
 * cannot be steered into navigating to a user- or backend-controlled location.
 */

export const POST_SIGN_IN_DESTINATION = "/api/auth/landing" as const;

export interface PostSignInSuccess {
  ok: true;
  destination: typeof POST_SIGN_IN_DESTINATION;
}

export interface PostSignInFailure {
  ok: false;
  error: string;
}

export type PostSignInResult = PostSignInSuccess | PostSignInFailure;

/**
 * The success result a successful sign-in returns. The Server Action does NOT
 * perform server-side navigation: it returns this value and the client owns the
 * full-document navigation boundary, so no stale RSC / Server Action navigation
 * state can cross authentication.
 */
export function postSignInSuccess(): PostSignInSuccess {
  return { ok: true, destination: POST_SIGN_IN_DESTINATION };
}

export type PostSignInNavigation =
  | { shouldNavigate: true; destination: typeof POST_SIGN_IN_DESTINATION }
  | { shouldNavigate: false };

/**
 * Pure decision the login form consults after a sign-in attempt.
 *
 * Navigates only when the state is exactly a success result whose destination
 * equals the static constant. Every other shape - failures, malformed state, a
 * destination that differs by even one character - yields no navigation.
 */
export function postSignInNavigation(state: unknown): PostSignInNavigation {
  if (
    state !== null &&
    typeof state === "object" &&
    (state as { ok?: unknown }).ok === true &&
    (state as { destination?: unknown }).destination ===
      POST_SIGN_IN_DESTINATION
  ) {
    return {
      shouldNavigate: true,
      destination: POST_SIGN_IN_DESTINATION,
    };
  }
  return { shouldNavigate: false };
}