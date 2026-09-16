import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  performLogout,
  type LogoutDeps,
} from "./logout-helpers.ts";
import type { StoredSession } from "@nhost/nhost-js/session";

const validSession: StoredSession = {
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
  decodedToken: {
    exp: Math.floor(Date.now() / 1000) + 900,
    iat: Math.floor(Date.now() / 1000),
    sub: "user-uuid",
    "https://hasura.io/jwt/claims": {
      "x-hasura-user-id": "user-uuid",
      "x-hasura-default-role": "user",
      "x-hasura-allowed-roles": ["user"],
    },
  },
};

const sessionNoRefresh: StoredSession = {
  ...validSession,
  refreshToken: "",
};

function createDeps(overrides: Partial<{
  session: StoredSession | null | undefined;
  signOutSuccess: boolean;
  clearThrows: boolean;
  deleteThrows: boolean;
  deleteOrgThrows: boolean;
}> = {}): LogoutDeps & {
  __testAccess: {
    signOutCalled: boolean;
    clearCalled: boolean;
    deleteCalled: boolean;
    deleteOrgCalled: boolean;
    lastRefreshToken: string;
  };
} {
  const hasSessionOverride = Object.prototype.hasOwnProperty.call(overrides, "session");
  const session = hasSessionOverride ? overrides.session : validSession;
  const signOutSuccess = overrides.signOutSuccess ?? true;
  const clearThrows = overrides.clearThrows ?? false;
  const deleteThrows = overrides.deleteThrows ?? false;
  const deleteOrgThrows = overrides.deleteOrgThrows ?? false;

  let clearCalled = false;
  let deleteCalled = false;
  let deleteOrgCalled = false;
  let signOutCalled = false;
  let lastRefreshToken = "";

  return {
    getSession: () => session ?? null,
    signOut: async (refreshToken: string) => {
      signOutCalled = true;
      lastRefreshToken = refreshToken;
      if (!signOutSuccess) {
        throw new Error("Network error");
      }
      return { status: 200, body: "OK" };
    },
    clearSession: () => {
      clearCalled = true;
      if (clearThrows) throw new Error("Clear failed");
    },
    deleteCookie: () => {
      deleteCalled = true;
      if (deleteThrows) throw new Error("Delete failed");
    },
    deleteOrganizationCookie: () => {
      deleteOrgCalled = true;
      if (deleteOrgThrows) throw new Error("Delete org cookie failed");
    },
    __testAccess: {
      get signOutCalled() { return signOutCalled; },
      get clearCalled() { return clearCalled; },
      get deleteCalled() { return deleteCalled; },
      get deleteOrgCalled() { return deleteOrgCalled; },
      get lastRefreshToken() { return lastRefreshToken; },
    },
  };
}

describe("performLogout", () => {
  it("A: remote signOut succeeds, local cleared, remoteRevoked true", async () => {
    const deps = createDeps({ session: validSession, signOutSuccess: true });
    const result = await performLogout(deps);

    assert.equal(result.localCleared, true);
    assert.equal(result.remoteRevoked, true);
    assert.equal(result.error, undefined);
    assert.equal(deps.__testAccess.signOutCalled, true);
    assert.equal(deps.__testAccess.lastRefreshToken, "refresh-token-uuid");
    assert.equal(deps.__testAccess.clearCalled, true);
    assert.equal(deps.__testAccess.deleteCalled, true);
    assert.equal(deps.__testAccess.deleteOrgCalled, true);
  });

  it("B: remote signOut fails, local still cleared, safe partial error", async () => {
    const deps = createDeps({ session: validSession, signOutSuccess: false });
    const result = await performLogout(deps);

    assert.equal(result.localCleared, true);
    assert.equal(result.remoteRevoked, false);
    assert.ok(result.error?.includes("Remote session revocation could not be confirmed"));
    assert.ok(!result.error?.includes("refresh-token-uuid"));
    assert.ok(!result.error?.includes("Network error"));
    assert.equal(deps.__testAccess.clearCalled, true);
    assert.equal(deps.__testAccess.deleteCalled, true);
    assert.equal(deps.__testAccess.deleteOrgCalled, true);
  });

  it("C: session has no refresh token, local cleared, no remote call, correct semantics", async () => {
    const deps = createDeps({ session: sessionNoRefresh });
    const result = await performLogout(deps);

    assert.equal(result.localCleared, true);
    assert.equal(result.remoteRevoked, false);
    assert.ok(result.error?.includes("missing refresh token"));
    assert.equal(deps.__testAccess.signOutCalled, false);
    assert.equal(deps.__testAccess.clearCalled, true);
    assert.equal(deps.__testAccess.deleteCalled, true);
  });

  it("D: no existing session, idempotent, local cleared, no remote call", async () => {
    const deps = createDeps({ session: null });
    const result = await performLogout(deps);

    assert.equal(result.localCleared, true);
    assert.equal(result.remoteRevoked, false);
    assert.equal(result.error, undefined);
    assert.equal(deps.__testAccess.signOutCalled, false);
    assert.equal(deps.__testAccess.clearCalled, true);
    assert.equal(deps.__testAccess.deleteCalled, true);
  });

  it("E: successful path never logs tokens", async () => {
    const deps = createDeps({ session: validSession, signOutSuccess: true });
    const result = await performLogout(deps);

    assert.equal(result.localCleared, true);
    assert.equal(result.remoteRevoked, true);
    assert.ok(!JSON.stringify(result).includes("refresh-token-uuid"));
    assert.ok(!JSON.stringify(result).includes("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"));
  });

  it("clearSession throws but cookie still deleted and localCleared true", async () => {
    const deps = createDeps({ session: validSession, clearThrows: true });
    const result = await performLogout(deps);

    assert.equal(result.localCleared, true);
    assert.equal(deps.__testAccess.deleteCalled, true);
  });

  it("deleteCookie throws but clearSession succeeds and localCleared true", async () => {
    const deps = createDeps({ session: validSession, deleteThrows: true });
    const result = await performLogout(deps);

    assert.equal(result.localCleared, true);
    assert.equal(deps.__testAccess.clearCalled, true);
  });

  it("both clear and delete throw: localCleared false, error returned", async () => {
    const deps = createDeps({ session: validSession, clearThrows: true, deleteThrows: true });
    const result = await performLogout(deps);

    assert.equal(result.localCleared, false);
    assert.ok(result.error !== undefined);
  });
});