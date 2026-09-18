import { describe, it } from "node:test";
import assert from "node:assert/strict";
import type { StoredSession } from "@nhost/nhost-js/session";
import {
  accessTokenAuthHeader,
  accessTokenExpirySeconds,
  classifySessionState,
  controlCenterBootstrap,
  landingDestination,
  loginPageDecision,
  protectedDecision,
  readSession,
  sanitizeNextPath,
} from "./read.ts";

const FUTURE = Math.floor(Date.now() / 1000) + 900;
const PAST = Math.floor(Date.now() / 1000) - 60;

function makeSession(overrides: Partial<StoredSession> = {}): StoredSession {
  return {
    accessToken: "test-access-token",
    accessTokenExpiresIn: 900,
    refreshToken: "refresh-token-uuid",
    refreshTokenId: "refresh-token-id-uuid",
    user: {
      id: "user-uuid",
      email: "test@example.com",
      displayName: "Test User",
      locale: "en",
      metadata: {},
      createdAt: "2024-01-01T00:00:00.000Z",
      defaultRole: "user",
      avatarUrl: "",
      emailVerified: false,
      isAnonymous: false,
      phoneNumberVerified: false,
      phoneNumber: "",
      roles: [],
      activeMfaType: undefined,
    },
    decodedToken: { exp: FUTURE, iat: FUTURE - 900, sub: "user-uuid" },
    ...overrides,
  };
}

// 1. A stale (expired) session on /login must never 500 Server Component
//    rendering: it redirects to the recovery handler instead.
describe("loginPageDecision", () => {
  it("stale session: recovery decision, never throws", () => {
    assert.equal(loginPageDecision("stale"), "recover");
  });

  it("missing session: renders the login form", () => {
    assert.equal(loginPageDecision("missing"), "render");
  });

  it("valid session: forwarded to the landing handler", () => {
    assert.equal(loginPageDecision("valid"), "forward");
  });
});

// 2. + 3. The RSC read path is a pure function over the cookie string: it
//    never sets or deletes a cookie. This module is intentionally free of
//    `next/headers`, so it also imports and runs under `node --test` outside
//    of a Next.js request context.
describe("readSession (RSC read path)", () => {
  it("2/3. reads from the cookie string without mutating cookies", () => {
    let reads = 0;
    const state = readSession(() => {
      reads += 1;
      return JSON.stringify(makeSession());
    });
    assert.equal(state, "valid");
    assert.equal(reads, 1);
  });

  it("malformed cookie string is 'missing', not a crash", () => {
    assert.equal(readSession(() => "not-json"), "missing");
    assert.equal(readSession(() => null), "missing");
  });

  it("cookie missing refresh token is 'missing' (unusable)", () => {
    const missingRefresh = makeSession({ refreshToken: "" });
    assert.equal(
      classifySessionState(missingRefresh),
      "missing",
    );
  });
});

// 4. An expired session hitting an authenticated (protected) route must
//    redirect safely to the recovery handler instead of rendering stale.
describe("protectedDecision", () => {
  it("expired authenticated route redirects safely (stale -> recover)", () => {
    assert.equal(protectedDecision("stale"), "recover");
  });

  it("valid session on a protected route is allowed through", () => {
    assert.equal(protectedDecision("valid"), "allow");
  });

  it("missing session on a protected route goes to login", () => {
    assert.equal(protectedDecision("missing"), "login");
  });
});

describe("classifySessionState", () => {
  it("expired access token is stale (fail closed)", () => {
    const expired = makeSession({ decodedToken: { exp: PAST, sub: "u" } });
    assert.equal(classifySessionState(expired), "stale");
  });

  it("not-yet-expired access token is valid", () => {
    assert.equal(classifySessionState(makeSession()), "valid");
  });

  it("undecodable expiry is stale (fail closed), never valid", () => {
    const undecodable = makeSession({
      decodedToken: { exp: undefined, sub: "u" },
      accessToken: "garbage-no-dots",
    });
    assert.equal(classifySessionState(undecodable), "stale");
  });

  it("null session is missing", () => {
    assert.equal(classifySessionState(null), "missing");
  });
});

describe("accessTokenExpirySeconds", () => {
  it("prefers the decodedToken.exp when present", () => {
    assert.equal(accessTokenExpirySeconds(makeSession()), FUTURE);
  });

  it("falls back to decoding the JWT payload", () => {
    const manualExp = FUTURE + 123;
    const header = Buffer.from(JSON.stringify({ alg: "HS256" })).toString("base64url");
    const payload = Buffer.from(JSON.stringify({ exp: manualExp })).toString("base64url");
    const session = makeSession({
      decodedToken: { exp: undefined, sub: "u" },
      accessToken: `${header}.${payload}.signature`,
    });
    assert.equal(accessTokenExpirySeconds(session), manualExp);
  });

  it("returns null for an undecodable token", () => {
    const noExp = { exp: undefined, sub: "u" };
    assert.equal(accessTokenExpirySeconds(makeSession({ accessToken: "x.y", decodedToken: noExp })), null);
    assert.equal(accessTokenExpirySeconds(null), null);
  });
});

// 7. Recovery must never bounce the user to an external / protocol-relative /
//    malformed destination supplied via ?next=.
describe("sanitizeNextPath", () => {
  it("7. rejects external absolute URLs (open-redirect guard)", () => {
    assert.equal(sanitizeNextPath("https://evil.example/dashboard"), null);
  });

  it("rejects protocol-relative URLs", () => {
    assert.equal(sanitizeNextPath("//evil.example/path"), null);
  });

  it("rejects backslashes and embedded schemes", () => {
    assert.equal(sanitizeNextPath("/a\\b"), null);
    assert.equal(sanitizeNextPath("/javascript:alert(1)"), null);
    assert.equal(sanitizeNextPath("/url.com://foo"), null);
  });

  it("rejects control / non-printable characters", () => {
    assert.equal(sanitizeNextPath("/a\u0000b"), null);
    assert.equal(sanitizeNextPath("/a b\u007fb"), null);
  });

  it("rejects empty / non-path values", () => {
    assert.equal(sanitizeNextPath(""), null);
    assert.equal(sanitizeNextPath("dashboard"), null);
    assert.equal(sanitizeNextPath(null), null);
    assert.equal(sanitizeNextPath(undefined), null);
  });

  it("accepts plain same-origin paths", () => {
    assert.equal(sanitizeNextPath("/dashboard"), "/dashboard");
    assert.equal(sanitizeNextPath("/select-organization"), "/select-organization");
    assert.equal(sanitizeNextPath("/a/b?c=d"), "/a/b?c=d");
  });
});

describe("landingDestination", () => {
  it("zero memberships -> no-organization", () => {
    assert.equal(landingDestination([], null), "/no-organization");
  });

  it("single membership -> dashboard", () => {
    assert.equal(landingDestination([{ id: 7 }], null), "/dashboard");
  });

  it("multiple memberships with valid selection -> dashboard", () => {
    assert.equal(
      landingDestination([{ id: 1 }, { id: 2 }], 2),
      "/dashboard",
    );
  });

  it("multiple memberships without a valid selection -> select-organization", () => {
    assert.equal(landingDestination([{ id: 1 }, { id: 2 }], null), "/select-organization");
    assert.equal(landingDestination([{ id: 1 }, { id: 2 }], 99), "/select-organization");
  });
});

describe("accessTokenAuthHeader", () => {
  it("builds a Bearer header from a cookie session without refreshing", () => {
    assert.equal(accessTokenAuthHeader(makeSession()), "Bearer test-access-token");
  });

  it("returns null when there is no session or no access token", () => {
    assert.equal(accessTokenAuthHeader(null), null);
    assert.equal(accessTokenAuthHeader(makeSession({ accessToken: "" })), null);
  });
});

// Deep-link regression: /dashboard, /tickets, /customers/123, /approvals are
// all under the (control-center) route group and share this layout. The layout
// render must never set/delete/refresh cookies (Next.js throws on cookie
// mutation outside a Server Action or Route Handler). `controlCenterBootstrap`
// is the pure decision the layout consults before any render.
describe("controlCenterBootstrap (deep-link / no-RSC-mutation)", () => {
  it("stale session on any control-center deep link -> recover (never render, never 500)", () => {
    assert.equal(controlCenterBootstrap("stale", [{ id: 1 }], 1), "recover");
    assert.equal(controlCenterBootstrap("stale", [{ id: 1 }], null), "recover");
    assert.equal(controlCenterBootstrap("stale", [{ id: 1 }, { id: 2 }], 999), "recover");
    assert.equal(controlCenterBootstrap("stale", [], null), "recover");
  });

  it("missing session -> login", () => {
    assert.equal(controlCenterBootstrap("missing", [], null), "login");
  });

  it("valid session + single membership without org cookie -> landing (handler persists)", () => {
    assert.equal(controlCenterBootstrap("valid", [{ id: 7 }], null), "landing");
  });

  it("valid session + stale/absent organization selection -> landing (handler re-resolves + clears)", () => {
    assert.equal(controlCenterBootstrap("valid", [{ id: 1 }, { id: 2 }], 99), "landing");
    assert.equal(controlCenterBootstrap("valid", [{ id: 1 }, { id: 2 }], null), "landing");
    assert.equal(controlCenterBootstrap("valid", [{ id: 1 }], 99), "landing");
  });

  it("valid session + valid selection -> render (no cookie mutation needed)", () => {
    assert.equal(controlCenterBootstrap("valid", [{ id: 1 }, { id: 2 }], 2), "render");
  });

  it("valid session + zero memberships -> no-organization", () => {
    assert.equal(controlCenterBootstrap("valid", [], null), "no-organization");
  });
});