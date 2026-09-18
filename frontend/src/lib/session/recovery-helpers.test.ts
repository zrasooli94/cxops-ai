import { describe, it } from "node:test";
import assert from "node:assert/strict";
import type { StoredSession } from "@nhost/nhost-js/session";
import {
  performSessionRecovery,
  type SessionRecoveryDeps,
} from "./recovery-helpers.ts";

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

function createDeps(overrides: Partial<{
  session: StoredSession | null;
  refreshResult: StoredSession | null;
  refreshThrows: boolean;
  clearThrows: boolean;
  safeNext: string | null;
}> = {}): SessionRecoveryDeps & {
  __testAccess: {
    refreshCalls: number;
    clearCalls: number;
    deleteCookieCalls: number;
    deleteOrgCookieCalls: number;
  };
} {
  const hasSessionOverride = Object.prototype.hasOwnProperty.call(overrides, "session");
  const session = hasSessionOverride ? overrides.session : makeSession({ decodedToken: { exp: PAST, sub: "u" } });
  const refreshResult = overrides.refreshResult ?? null;
  const refreshThrows = overrides.refreshThrows ?? false;
  const clearThrows = overrides.clearThrows ?? false;
  const safeNext = overrides.safeNext ?? null;

  let refreshCalls = 0;
  let clearCalls = 0;
  let deleteCookieCalls = 0;
  let deleteOrgCookieCalls = 0;

  return {
    readSession: () => session,
    nowSeconds: () => Math.floor(Date.now() / 1000),
    refresh: async () => {
      refreshCalls += 1;
      if (refreshThrows) throw new Error("Refresh failed");
      return refreshResult;
    },
    clearSession: () => {
      clearCalls += 1;
      if (clearThrows) throw new Error("Clear failed");
    },
    deleteSessionCookie: () => {
      deleteCookieCalls += 1;
    },
    deleteOrganizationCookie: () => {
      deleteOrgCookieCalls += 1;
    },
    safeNext,
    __testAccess: {
      get refreshCalls() { return refreshCalls; },
      get clearCalls() { return clearCalls; },
      get deleteCookieCalls() { return deleteCookieCalls; },
      get deleteOrgCookieCalls() { return deleteOrgCookieCalls; },
    },
  };
}

describe("performSessionRecovery", () => {
  it("missing session: goes to login, never refreshes", async () => {
    const deps = createDeps({ session: null });
    const result = await performSessionRecovery(deps);

    assert.equal(result.outcome, "missing");
    assert.equal(result.destination, "/login");
    assert.equal(deps.__testAccess.refreshCalls, 0);
    assert.equal(deps.__testAccess.clearCalls, 0);
  });

  it("valid session: goes to safe destination, never refreshes", async () => {
    const deps = createDeps({
      session: makeSession(),
      safeNext: "/select-organization",
    });
    const result = await performSessionRecovery(deps);

    assert.equal(result.outcome, "valid");
    assert.equal(result.destination, "/select-organization");
    assert.equal(deps.__testAccess.refreshCalls, 0);
  });

  it("valid session without safeNext defaults to /dashboard", async () => {
    const deps = createDeps({ session: makeSession() });
    const result = await performSessionRecovery(deps);

    assert.equal(result.destination, "/dashboard");
    assert.equal(deps.__testAccess.refreshCalls, 0);
  });

  it("6. stale session + failed refresh: cleared, both cookies deleted, /login", async () => {
    const deps = createDeps({ session: makeSession({ decodedToken: { exp: PAST, sub: "u" } }) });
    const result = await performSessionRecovery(deps);

    assert.equal(result.outcome, "cleared");
    assert.equal(result.destination, "/login");
    assert.equal(deps.__testAccess.refreshCalls, 1);
    assert.equal(deps.__testAccess.clearCalls, 1);
    assert.equal(deps.__testAccess.deleteCookieCalls, 1);
    assert.equal(deps.__testAccess.deleteOrgCookieCalls, 1);
  });

  it("10. active-org cookie cleared on full invalidation even when clearSession throws", async () => {
    const deps = createDeps({
      session: makeSession({ decodedToken: { exp: PAST, sub: "u" } }),
      clearThrows: true,
    });
    const result = await performSessionRecovery(deps);

    assert.equal(result.outcome, "cleared");
    assert.equal(deps.__testAccess.deleteCookieCalls, 1);
    assert.equal(deps.__testAccess.deleteOrgCookieCalls, 1);
  });

  it("stale session + refresh throws: cleared exactly once, single attempt", async () => {
    const deps = createDeps({
      session: makeSession({ decodedToken: { exp: PAST, sub: "u" } }),
      refreshThrows: true,
      safeNext: "/dashboard/settings",
    });
    const result = await performSessionRecovery(deps);

    assert.equal(result.outcome, "cleared");
    assert.equal(result.destination, "/login");
    assert.equal(deps.__testAccess.refreshCalls, 1);
  });

  it("stale session + successful refresh: recovered, exactly one refresh", async () => {
    const refreshed = makeSession();
    const deps = createDeps({
      session: makeSession({ decodedToken: { exp: PAST, sub: "u" } }),
      refreshResult: refreshed,
      safeNext: "/dashboard",
    });
    const result = await performSessionRecovery(deps);

    assert.equal(result.outcome, "recovered");
    assert.equal(result.destination, "/dashboard");
    assert.equal(deps.__testAccess.refreshCalls, 1);
    assert.equal(deps.__testAccess.clearCalls, 0);
    assert.equal(deps.__testAccess.deleteCookieCalls, 0);
  });

  it("11. never leaks tokens in the returned result", async () => {
    const deps = createDeps({
      session: makeSession({ decodedToken: { exp: PAST, sub: "u" } }),
    });
    const result = await performSessionRecovery(deps);

    assert.ok(!JSON.stringify(result).includes("test-access-token"));
    assert.ok(!JSON.stringify(result).includes("refresh-token-uuid"));
    assert.ok(!JSON.stringify(result).includes("nhostSession"));
  });
});