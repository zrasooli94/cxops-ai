import type { StoredSession } from "@nhost/nhost-js/session";
import { deserializeSession } from "./helpers.ts";
import { AuthorizationLoadError } from "../authorization/helpers.ts";

/**
 * Read-only session classification. This module MUST stay free of Next.js
 * request APIs (`next/headers`) and of the Nhost client: it is used during
 * Server Component rendering, where cookie mutations are forbidden, and it is
 * unit-tested with `node --test`, which cannot resolve `next/headers`.
 *
 * The single exception is the `deserializeSession` import from `./helpers`,
 * which is pure (JSON parsing only).
 */

export type SessionState = "missing" | "stale" | "valid";

export type LoginDecision = "render" | "recover" | "forward";
export type ProtectedDecision = "login" | "recover" | "allow";

/**
 * Return the JWT `exp` (unix seconds) of the access token, or null when the
 * token is missing or its expiry cannot be decoded. Prefers the SDK-parsed
 * `decodedToken.exp`; falls back to decoding the JWT payload directly.
 */
export function accessTokenExpirySeconds(
  session: StoredSession | null,
): number | null {
  if (!session?.accessToken) return null;

  const decodedExp = session.decodedToken?.exp;
  if (typeof decodedExp === "number") return decodedExp;

  try {
    const payloadSegment = session.accessToken.split(".")[1];
    if (!payloadSegment) return null;
    const json = Buffer.from(payloadSegment, "base64url").toString("utf8");
    const payload = JSON.parse(json) as { exp?: number };
    return typeof payload.exp === "number" ? payload.exp : null;
  } catch {
    return null;
  }
}

/**
 * Classify a cookie-backed session from the point of view of READ-ONLY code.
 *
 * - any unusable cookie (missing, or missing access/refresh/user) -> "missing"
 * - an access token that cannot be decoded or has already expired -> "stale"
 *   (fail closed: the caller must recover through a mutable context)
 * - otherwise -> "valid"
 */
export function classifySessionState(
  session: StoredSession | null,
  nowSeconds: number = Math.floor(Date.now() / 1000),
): SessionState {
  if (!session) return "missing";
  if (!session.accessToken || !session.refreshToken || !session.user) {
    return "missing";
  }
  const exp = accessTokenExpirySeconds(session);
  if (exp === null) return "stale";
  if (nowSeconds >= exp) return "stale";
  return "valid";
}

/** Read and classify the raw session-cookie value. */
export function readSession(reader: () => string | null): SessionState {
  return classifySessionState(deserializeSession(reader()));
}

/** What the login page should do for a given session state. */
export function loginPageDecision(state: SessionState): LoginDecision {
  if (state === "missing") return "render";
  if (state === "stale") return "recover";
  return "forward";
}

/** What an authenticated (protected) page should do for a given state. */
export function protectedDecision(state: SessionState): ProtectedDecision {
  if (state === "missing") return "login";
  if (state === "stale") return "recover";
  return "allow";
}

/**
 * Authorization header built from a cookie-backed session WITHOUT refreshing.
 * Used by read-only backend calls from Server Components, where refreshing
 * would attempt a cookie write during render (a Next.js error).
 */
export function accessTokenAuthHeader(
  session: StoredSession | null,
): string | null {
  return session?.accessToken ? `Bearer ${session.accessToken}` : null;
}

/**
 * Where the control-center layout should send the request, decided purely from
 * the classified session state, the membership list, and the active-org cookie.
 *
 * The layout render itself must never persist or clear the tenant-selection
 * cookie nor refresh the session (both mutate cookies during render). Every
 * decision that would need such a mutation routes to a Route Handler instead:
 * - "recover" -> /api/auth/session   (one refresh, clears on failure)
 * - "landing" -> /api/auth/landing   (persists/clears the org cookie)
 */
export type ControlCenterBootstrap =
  | "login"
  | "recover"
  | "no-organization"
  | "landing"
  | "render";

/**
 * Where a successful sign-in sends the browser.
 *
 * The landing Route Handler is the canonical post-auth bootstrap owner: it
 * resolves memberships, initializes the active-organization cookie, and routes
 * to /dashboard (single org), /select-organization (multiple orgs), or
 * /no-organization (zero memberships).
 *
 * Sign-in must NOT jump straight to /dashboard: on a first login there is no
 * active-organization cookie yet, so the layout would immediately bounce
 * through the landing handler anyway, and a fresh /dashboard pass before the
 * cookie existed made Safari sometimes show a transient "This page couldn't
 * load" at the end of the redirect chain.
 */
export const POST_SIGN_IN_DESTINATION = "/api/auth/landing" as const;

export function controlCenterBootstrap(
  state: SessionState,
  memberships: LandingOrganization[],
  activeOrganizationId: number | null,
): ControlCenterBootstrap {
  const decision = protectedDecision(state);
  if (decision === "login") return "login";
  if (decision === "recover") return "recover";

  if (memberships.length === 0) return "no-organization";

  const selected =
    activeOrganizationId !== null &&
    memberships.some((m) => m.id === activeOrganizationId);

  // A single membership whose id matches the active-org cookie renders here
  // directly; only an absent or incompatible single selection needs the landing
  // Route Handler to persist/replace the org cookie. Returning "landing" for a
  // matching single membership is the pre-fix bug that caused the production
  // /dashboard <-> /api/auth/landing infinite redirect loop.
  if (memberships.length === 1) return selected ? "render" : "landing";

  // An invalid/absent multi-membership selection needs the landing handler to
  // clear the org cookie and route to the selector.
  if (!selected) return "landing";

  return "render";
}

/**
 * How the landing Route Handler should react when loading memberships fails.
 *
 * The mutable membership load can fail either because authentication broke
 * (refresh failed or the backend rejected the token) or because the backend is
 * down / returned an unusable response. Distinguishing these is what stops the
 * landing endpoint from 500-ing with internal detail.
 *
 * - "recover" -> run the session recovery handler: exactly one refresh, and on
 *   failure it clears the session and org cookies and goes to /login.
 * - "fail-safe" -> any other failure: answer generically, never throw. Only the
 *   typed `AuthorizationLoadError` with reason `authentication` is trusted to
 *   mean recovery; everything else is treated as an outage.
 */
export type LandingMembershipLoadDisposition = "recover" | "fail-safe";

export function landingMembershipLoadDisposition(
  error: unknown,
): LandingMembershipLoadDisposition {
  if (
    error instanceof AuthorizationLoadError &&
    error.reason === "authentication"
  ) {
    return "recover";
  }
  return "fail-safe";
}

/**
 * Sanitize a `?next=` value into a same-origin path that is safe to redirect
 * to. Returns null for anything that is not a plain local path: absolute URLs,
 * protocol-relative URLs, backslashes, control / non-printable characters, or
 * embedded schemes.
 */
export function sanitizeNextPath(
  raw: string | null | undefined,
): string | null {
  if (typeof raw !== "string" || raw.length === 0) return null;
  if (!raw.startsWith("/")) return null;
  if (raw.startsWith("//")) return null;
  if (raw.includes("\\")) return null;
  if (raw.includes("://")) return null;
  // Printable ASCII only: rejects control characters, lone high bytes, spaces
  // are allowed but uncommon; a space inside a path is still same-origin.
  if (/[\u0000-\u001f\u007f-\uffff]/.test(raw)) return null;
  // Belt-and-braces: reject a scheme-like first segment ("/javascript:...").
  if (/^\/[^/]*:/.test(raw)) return null;
  return raw;
}

export interface LandingOrganization {
  id: number;
}

/**
 * Decide the post-auth destination from the subject's memberships and the
 * currently selected organization. Pure decision; cookie side effects belong
 * to the caller.
 */
export function landingDestination(
  memberships: LandingOrganization[],
  activeOrganizationId: number | null,
): string {
  if (memberships.length === 0) return "/no-organization";
  if (memberships.length === 1) return "/dashboard";
  if (
    activeOrganizationId !== null &&
    memberships.some((m) => m.id === activeOrganizationId)
  ) {
    return "/dashboard";
  }
  return "/select-organization";
}