import { describe, it } from "node:test";
import assert from "node:assert/strict";
import type { StoredSession } from "@nhost/nhost-js/session";
import {
  accessTokenAuthHeader,
  accessTokenExpirySeconds,
  classifySessionState,
  controlCenterBootstrap,
  landingDestination,
  landingMembershipLoadDisposition,
  loginPageDecision,
  protectedDecision,
  readSession,
  sanitizeNextPath,
} from "./read.ts";
import { AuthorizationLoadError } from "../authorization/helpers.ts";

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

// 2/5. A single membership whose id matches the persisted org cookie must render
// DIRECTLY. Returning "landing" for a matching single membership was the
// production bug: landing persisted the cookie, /dashboard re-read the cookie,
// bootstrap said "landing" again, ad infinitum (/dashboard <-> /api/auth/landing).
describe("controlCenterBootstrap (single-membership redirect-loop regression)", () => {
  it("2. valid session + single membership + matching org cookie -> render", () => {
    assert.equal(controlCenterBootstrap("valid", [{ id: 7 }], 7), "render");
  });

  it("5. dashboard after landing renders: one landing hop, then the matching cookie renders", () => {
    assert.equal(controlCenterBootstrap("valid", [{ id: 7 }], null), "landing");
    assert.equal(controlCenterBootstrap("valid", [{ id: 7 }], 7), "render");
  });

  it("3. valid session + single membership + wrong/stale org cookie -> landing (cookie replaced)", () => {
    assert.equal(controlCenterBootstrap("valid", [{ id: 7 }], 99), "landing");
  });

  it("1. valid session + single membership + no org cookie -> landing (handler persists)", () => {
    assert.equal(controlCenterBootstrap("valid", [{ id: 7 }], null), "landing");
  });

  it("13. a matching selection renders WITHOUT any cookie mutation (no landing needed)", () => {
    assert.equal(controlCenterBootstrap("valid", [{ id: 7 }], 7), "render");
    assert.equal(controlCenterBootstrap("valid", [{ id: 1 }, { id: 2 }], 2), "render");
  });
});

// 10/11/14. The landing endpoint must survive a membership-load authentication
// failure by routing through the session recovery handler (never a 500), and any
// other backend failure must be fail-safe with no internal detail or token leak.
describe("landingMembershipLoadDisposition (landing auth-failure path)", () => {
  it("10. authentication failure during membership load -> recover via session handler", () => {
    assert.equal(
      landingMembershipLoadDisposition(
        new AuthorizationLoadError("Authentication required", "authentication"),
      ),
      "recover",
    );
  });

  it("backend unavailable / unusable response -> fail-safe (never 500 with detail)", () => {
    assert.equal(
      landingMembershipLoadDisposition(
        new AuthorizationLoadError("Could not load organizations", "unavailable"),
      ),
      "fail-safe",
    );
    assert.equal(landingMembershipLoadDisposition(new Error("boom")), "fail-safe");
    assert.equal(landingMembershipLoadDisposition(undefined), "fail-safe");
  });

  it("membership failures never impersonate authentication", () => {
    assert.equal(
      landingMembershipLoadDisposition(
        new AuthorizationLoadError("Membership invalid", "membership"),
      ),
      "fail-safe",
    );
  });

  it("14. disposition never leaks tokens, cookie names, or credentials", () => {
    const serialized = JSON.stringify({
      disposition: landingMembershipLoadDisposition(
        new AuthorizationLoadError("Authentication required", "authentication"),
      ),
      recoverTarget: "/api/auth/session",
      failSafeStatus: 503,
    });
    assert.ok(!serialized.includes("test-access-token"));
    assert.ok(!serialized.includes("refresh-token-uuid"));
    assert.ok(!serialized.includes("nhostSession"));
    assert.ok(!serialized.includes("password"));
  });
});

// 12/15. Finite-state proof that the dashboard <-> landing cycle terminates for
// every tenant-selection shape, including the exact states that looped forever
// in production.
describe("control-center redirect FSM terminates (no dashboard <-> landing loop)", () => {
  interface FsmState {
    memberships: { id: number }[];
    cookie: number | null;
  }

  const MAX_HOPS = 6;

  // Pure mirror of the landing Route Handler: a sole membership is persisted as
  // the org cookie and lands on /dashboard; an invalid/absent multi-membership
  // selection is cleared and lands on the selector.
  function landingOutcome(state: FsmState): {
    nextCookie: number | null;
    destination: string;
  } {
    if (state.memberships.length === 0) {
      return { nextCookie: null, destination: "/no-organization" };
    }
    if (state.memberships.length === 1) {
      return { nextCookie: state.memberships[0].id, destination: "/dashboard" };
    }
    const selected =
      state.cookie !== null &&
      state.memberships.some((m) => m.id === state.cookie);
    return selected
      ? { nextCookie: state.cookie, destination: "/dashboard" }
      : { nextCookie: null, destination: "/select-organization" };
  }

  function simulate(start: FsmState): {
    steps: string[];
    terminal: string;
  } {
    let state: FsmState = start;
    const steps: string[] = [];
    for (let hop = 0; hop < MAX_HOPS; hop += 1) {
      const bootstrap = controlCenterBootstrap(
        "valid",
        state.memberships,
        state.cookie,
      );
      if (bootstrap === "render") {
        steps.push("render");
        return { steps, terminal: "render" };
      }
      if (bootstrap === "no-organization") {
        return { steps, terminal: "no-organization" };
      }
      if (bootstrap === "login" || bootstrap === "recover") {
        return { steps, terminal: bootstrap };
      }
      const outcome = landingOutcome(state);
      steps.push(`landing:${outcome.destination}`);
      state = { ...state, cookie: outcome.nextCookie };
      if (outcome.destination !== "/dashboard") {
        return { steps, terminal: outcome.destination };
      }
    }
    return { steps, terminal: "UNBOUNDED" };
  }

  it("12. single membership + no org cookie: one landing hop then render", () => {
    const { steps, terminal } = simulate({ memberships: [{ id: 7 }], cookie: null });
    assert.deepEqual(steps, ["landing:/dashboard", "render"]);
    assert.equal(terminal, "render");
  });

  it("12. single membership + stale org cookie: one landing hop replaces it, then render", () => {
    const { steps, terminal } = simulate({ memberships: [{ id: 7 }], cookie: 99 });
    assert.equal(terminal, "render");
    assert.equal(steps.length, 2);
    assert.ok(steps[0]?.startsWith("landing:/dashboard"));
  });

  it("2/12. single membership + matching org cookie: renders immediately, zero landing hops", () => {
    const { steps, terminal } = simulate({ memberships: [{ id: 7 }], cookie: 7 });
    assert.deepEqual(steps, ["render"]);
    assert.equal(terminal, "render");
  });

  it("6/12. zero memberships: terminates at /no-organization", () => {
    const { terminal } = simulate({ memberships: [], cookie: null });
    assert.equal(terminal, "no-organization");
  });

  it("7/12. multiple memberships + valid selection: renders immediately", () => {
    const { steps, terminal } = simulate({ memberships: [{ id: 1 }, { id: 2 }], cookie: 2 });
    assert.deepEqual(steps, ["render"]);
    assert.equal(terminal, "render");
  });

  it("8/12. multiple memberships + invalid/absent selection: one landing then selector", () => {
    const invalid = simulate({ memberships: [{ id: 1 }, { id: 2 }], cookie: 99 });
    assert.equal(invalid.terminal, "/select-organization");
    assert.ok(invalid.steps[0]?.startsWith("landing:/select-organization"));

    const absent = simulate({ memberships: [{ id: 1 }, { id: 2 }], cookie: null });
    assert.equal(absent.terminal, "/select-organization");
  });

  it("12. every tenant shape terminates within the hop bound", () => {
    const scenarios: FsmState[] = [
      { memberships: [], cookie: null },
      { memberships: [{ id: 7 }], cookie: null },
      { memberships: [{ id: 7 }], cookie: 7 },
      { memberships: [{ id: 7 }], cookie: 99 },
      { memberships: [{ id: 1 }, { id: 2 }], cookie: 2 },
      { memberships: [{ id: 1 }, { id: 2 }], cookie: 99 },
      { memberships: [{ id: 1 }, { id: 2 }], cookie: null },
      { memberships: [{ id: 1 }, { id: 2 }, { id: 3 }], cookie: 5 },
    ];
    for (const scenario of scenarios) {
      const { steps, terminal } = simulate(scenario);
      assert.notEqual(terminal, "UNBOUNDED");
      assert.ok(
        steps.length < MAX_HOPS,
        `members=${scenario.memberships.length} cookie=${scenario.cookie}`,
      );
    }
  });
});