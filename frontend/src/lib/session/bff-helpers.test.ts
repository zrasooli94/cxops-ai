import { describe, it } from "node:test";
import assert from "node:assert/strict";
import type { StoredSession } from "@nhost/nhost-js/session";
import { refreshSessionOnce, type BffRefreshDeps } from "./bff-helpers.ts";

const FUTURE = Math.floor(Date.now() / 1000) + 900;

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
}> = {}): BffRefreshDeps & {
  __testAccess: {
    refreshCalls: number;
    clearCalls: number;
    deleteCookieCalls: number;
    deleteOrgCookieCalls: number;
    lastMargin: number;
  };
} {
  const hasSessionOverride = Object.prototype.hasOwnProperty.call(overrides, "session");
  const session = hasSessionOverride ? overrides.session : makeSession();
  const hasRefreshOverride = Object.prototype.hasOwnProperty.call(overrides, "refreshResult");
  const refreshResult = hasRefreshOverride
    ? overrides.refreshResult
    : makeSession({ accessToken: "new-access-token" });
  const refreshThrows = overrides.refreshThrows ?? false;
  const clearThrows = overrides.clearThrows ?? false;

  let refreshCalls = 0;
  let clearCalls = 0;
  let deleteCookieCalls = 0;
  let deleteOrgCookieCalls = 0;
  let lastMargin = -1;

  return {
    getSession: () => session,
    refresh: async (marginSeconds) => {
      refreshCalls += 1;
      lastMargin = marginSeconds;
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
    __testAccess: {
      get refreshCalls() { return refreshCalls; },
      get clearCalls() { return clearCalls; },
      get deleteCookieCalls() { return deleteCookieCalls; },
      get deleteOrgCookieCalls() { return deleteOrgCookieCalls; },
      get lastMargin() { return lastMargin; },
    },
  };
}

describe("refreshSessionOnce", () => {
  it("8. BFF refresh persists session: Bearer header, nothing cleared, single attempt", async () => {
    const deps = createDeps();
    const result = await refreshSessionOnce(deps, 60);

    assert.equal(result.authHeader, "Bearer new-access-token");
    assert.equal(result.cleared, false);
    assert.equal(deps.__testAccess.refreshCalls, 1);
    assert.equal(deps.__testAccess.lastMargin, 60);
    assert.equal(deps.__testAccess.clearCalls, 0);
    assert.equal(deps.__testAccess.deleteCookieCalls, 0);
    assert.equal(deps.__testAccess.deleteOrgCookieCalls, 0);
  });

  it("no access token: no header, nothing cleared, never refreshes", async () => {
    const deps = createDeps({ session: null });
    const result = await refreshSessionOnce(deps, 60);

    assert.equal(result.authHeader, null);
    assert.equal(result.cleared, false);
    assert.equal(deps.__testAccess.refreshCalls, 0);
    assert.equal(deps.__testAccess.clearCalls, 0);
    assert.equal(deps.__testAccess.deleteCookieCalls, 0);
  });

  it("9. BFF invalid refresh fails once: no header, cleared, both cookies deleted", async () => {
    const deps = createDeps({ refreshResult: null });
    const result = await refreshSessionOnce(deps, 0);

    assert.equal(result.authHeader, null);
    assert.equal(result.cleared, true);
    assert.equal(deps.__testAccess.refreshCalls, 1);
    assert.equal(deps.__testAccess.clearCalls, 1);
    assert.equal(deps.__testAccess.deleteCookieCalls, 1);
    assert.equal(deps.__testAccess.deleteOrgCookieCalls, 1);
  });

  it("10. refresh throwing clears the session and both cookies too", async () => {
    const deps = createDeps({ refreshThrows: true });
    const result = await refreshSessionOnce(deps, 60);

    assert.equal(result.authHeader, null);
    assert.equal(result.cleared, true);
    assert.equal(deps.__testAccess.refreshCalls, 1);
    assert.equal(deps.__testAccess.deleteOrgCookieCalls, 1);
  });

  it("11. cleared-failure result never leaks tokens or cookie names", async () => {
    const deps = createDeps({ refreshResult: null });
    const result = await refreshSessionOnce(deps, 60);

    assert.equal(result.authHeader, null);
    assert.ok(!JSON.stringify(result).includes("secret-token-123"));
    assert.ok(!JSON.stringify(result).includes("refresh-token-uuid"));
    assert.ok(!JSON.stringify(result).includes("nhostSession"));
  });
});