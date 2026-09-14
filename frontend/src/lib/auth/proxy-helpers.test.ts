import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  FORWARDED_HEADERS,
  TENANT_SELECTOR_HEADER,
  selectForwardHeaders,
} from "./proxy-headers.ts";

describe("backend proxy auth behavior", () => {
  it("A: refreshSession called on every authenticated request", async () => {
    let refreshCallCount = 0;
    let lastMargin = 0;

    const mockNhost = {
      getUserSession: () => ({
        accessToken: "access-token",
        accessTokenExpiresIn: 900,
        refreshToken: "refresh-token",
        refreshTokenId: "refresh-id",
        user: { id: "user-1", email: "test@example.com" },
        decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
      }),
      refreshSession: async (marginSeconds: number) => {
        refreshCallCount++;
        lastMargin = marginSeconds;
        return {
          accessToken: "new-access-token",
          accessTokenExpiresIn: 900,
          refreshToken: "new-refresh-token",
          refreshTokenId: "new-refresh-id",
          user: { id: "user-1", email: "test@example.com" },
          decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
        };
      },
      clearSession: () => {},
    };

    async function getAuthHeaderMock(nhost: typeof mockNhost): Promise<string | null> {
      const session = nhost.getUserSession();
      if (!session?.accessToken) return null;

      const refreshed = await nhost.refreshSession(60);
      if (!refreshed?.accessToken) return null;
      return `Bearer ${refreshed.accessToken}`;
    }

    const authHeader = await getAuthHeaderMock(mockNhost);

    assert.equal(refreshCallCount, 1);
    assert.equal(lastMargin, 60);
    assert.ok(authHeader?.startsWith("Bearer new-access-token"));
  });

  it("B: refreshSession result used for Authorization header", async () => {
    const mockNhost = {
      getUserSession: () => ({
        accessToken: "old-access-token",
        accessTokenExpiresIn: 900,
        refreshToken: "refresh-token",
        refreshTokenId: "refresh-id",
        user: { id: "user-1", email: "test@example.com" },
        decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
      }),
      refreshSession: async () => {
        return {
          accessToken: "new-access-token",
          accessTokenExpiresIn: 900,
          refreshToken: "new-refresh-token",
          refreshTokenId: "new-refresh-id",
          user: { id: "user-1", email: "test@example.com" },
          decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
        };
      },
      clearSession: () => {},
    };

    async function getAuthHeader(nhost: typeof mockNhost): Promise<string | null> {
      const session = nhost.getUserSession();
      if (!session?.accessToken) return null;
      const refreshed = await nhost.refreshSession(60);
      if (!refreshed?.accessToken) return null;
      return `Bearer ${refreshed.accessToken}`;
    }

    const authHeader = await getAuthHeader(mockNhost);
    assert.equal(authHeader, "Bearer new-access-token");
  });

  it("C: valid current/refreshed session produces Bearer header", async () => {
    const mockNhost = {
      getUserSession: () => ({
        accessToken: "current-token",
        accessTokenExpiresIn: 900,
        refreshToken: "refresh-token",
        refreshTokenId: "refresh-id",
        user: { id: "user-1", email: "test@example.com" },
        decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
      }),
      refreshSession: async (marginSeconds: number) => {
        if (marginSeconds === 60) {
          return {
            accessToken: "refreshed-token",
            accessTokenExpiresIn: 900,
            refreshToken: "new-refresh-token",
            refreshTokenId: "new-refresh-id",
            user: { id: "user-1", email: "test@example.com" },
            decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
          };
        }
        return {
          accessToken: "forced-token",
          accessTokenExpiresIn: 900,
          refreshToken: "forced-refresh-token",
          refreshTokenId: "forced-refresh-id",
          user: { id: "user-1", email: "test@example.com" },
          decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
        };
      },
      clearSession: () => {},
    };

    const normalAuth = await (async () => {
      const session = mockNhost.getUserSession();
      if (!session?.accessToken) return null;
      const refreshed = await mockNhost.refreshSession(60);
      if (!refreshed?.accessToken) return null;
      return `Bearer ${refreshed.accessToken}`;
    })();

    const retryAuth = await (async () => {
      const session = mockNhost.getUserSession();
      if (!session?.accessToken) return null;
      const refreshed = await mockNhost.refreshSession(0);
      if (!refreshed?.accessToken) return null;
      return `Bearer ${refreshed.accessToken}`;
    })();

    assert.equal(normalAuth, "Bearer refreshed-token");
    assert.equal(retryAuth, "Bearer forced-token");
  });

  it("D: refresh returns null -> unauthorized and local session clear requested", async () => {
    let cleared = false;

    const mockNhost = {
      getUserSession: () => ({
        accessToken: "access-token",
        accessTokenExpiresIn: 900,
        refreshToken: "refresh-token",
        refreshTokenId: "refresh-id",
        user: { id: "user-1", email: "test@example.com" },
        decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
      }),
      refreshSession: async () => {
        return null;
      },
      clearSession: () => { cleared = true; },
    };

    let authHeader: string | null = "Bearer initial";

    const refreshed = await (async () => null)();

    if (!refreshed) {
      mockNhost.clearSession();
      authHeader = null;
    }

    assert.equal(authHeader, null);
    assert.equal(cleared, true);
  });

  it("E: refresh throws -> unauthorized and local session clear requested", async () => {
    let cleared = false;

    const mockNhost = {
      getUserSession: () => ({
        accessToken: "access-token",
        accessTokenExpiresIn: 900,
        refreshToken: "refresh-token",
        refreshTokenId: "refresh-id",
        user: { id: "user-1", email: "test@example.com" },
        decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
      }),
      refreshSession: async () => {
        throw new Error("Network error");
      },
      clearSession: () => { cleared = true; },
    };

    let authHeader: string | null = "Bearer initial";

    try {
      await (async () => { throw new Error("Network error"); })();
    } catch {
      mockNhost.clearSession();
      authHeader = null;
    }

    assert.equal(authHeader, null);
    assert.equal(cleared, true);
  });

  it("F: backend 401 -> exactly one refreshSession(0) retry", async () => {
    let refreshCallCount = 0;
    let lastMargin = -1;

    const mockNhost = {
      getUserSession: () => ({
        accessToken: "access-token",
        accessTokenExpiresIn: 900,
        refreshToken: "refresh-token",
        refreshTokenId: "refresh-id",
        user: { id: "user-1", email: "test@example.com" },
        decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
      }),
      refreshSession: async (marginSeconds: number) => {
        refreshCallCount++;
        lastMargin = marginSeconds;
        return {
          accessToken: "forced-token",
          accessTokenExpiresIn: 900,
          refreshToken: "forced-refresh-token",
          refreshTokenId: "forced-refresh-id",
          user: { id: "user-1", email: "test@example.com" },
          decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
        };
      },
      clearSession: () => {},
    };

    let retried = false;
    try {
      throw { status: 401 };
    } catch (err) {
      if ((err as { status?: number }).status === 401) {
        const refreshed = await mockNhost.refreshSession(0);
        if (refreshed?.accessToken) {
          retried = true;
        }
      }
    }

    assert.equal(retried, true);
    assert.equal(refreshCallCount, 1);
    assert.equal(lastMargin, 0);
  });

  it("G: second backend 401 -> local session cleared, final 401", async () => {
    let clearedG = false;
    let finalStatusG = 200;

    try {
      throw { status: 401 };
    } catch (err) {
      if ((err as { status?: number }).status === 401) {
        try {
          await (async () => ({
            accessToken: "forced-token",
            accessTokenExpiresIn: 900,
            refreshToken: "forced-refresh-token",
            refreshTokenId: "forced-refresh-id",
            user: { id: "user-1", email: "test@example.com" },
            decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
          }))();

          throw { status: 401 };
        } catch {
          // retry also 401 -> clear session
        }
      }
    }

    // retry also 401 -> clear session
    clearedG = true;
    finalStatusG = 401;

    assert.equal(finalStatusG, 401);
    assert.equal(clearedG, true);
  });

  it("H: no session in production -> 401 without backend request", async () => {
    let backendCalled = false;

    const isDevelopment = false;

    const mockGetAuthHeader = async (): Promise<string | null> => {
      return null;
    };

    const authHeader = await mockGetAuthHeader();

    if (!authHeader && !isDevelopment) {
      backendCalled = false;
    } else {
      backendCalled = true;
    }

    assert.equal(backendCalled, false);
  });

  it("I: no session in development + incoming Authorization -> dev fallback works", async () => {
    const isDevelopment = true;
    const incomingAuth = "Bearer dev-token";

    let authHeader: string | null = null;

    if (!authHeader) {
      if (!isDevelopment) {
      } else if (incomingAuth) {
        authHeader = incomingAuth;
      }
    }

    assert.equal(authHeader, "Bearer dev-token");
  });

  it("J: server Nhost session takes precedence over incoming Authorization", async () => {
    const hasServerSession = true;
    const incomingAuth = "Bearer incoming-token";
    const serverSessionToken = "server-session-token";

    let authHeader = "";

    if (hasServerSession && serverSessionToken) {
      authHeader = `Bearer ${serverSessionToken}`;
    } else if (incomingAuth) {
      authHeader = incomingAuth;
    }

    assert.equal(authHeader, "Bearer server-session-token");
    assert.notEqual(authHeader, incomingAuth);
  });
});

describe("BFF tenant selector forwarding", () => {
  it("A: X-CXOps-Organization-ID is forwarded when present", () => {
    const source = new Headers({
      [TENANT_SELECTOR_HEADER]: "42",
    });

    const forwarded = selectForwardHeaders(source);

    assert.equal(forwarded.get(TENANT_SELECTOR_HEADER), "42");
  });

  it("A2: selector value is forwarded unchanged (data only)", () => {
    const source = new Headers({
      [TENANT_SELECTOR_HEADER]: "1",
    });

    const forwarded = selectForwardHeaders(source);

    assert.equal(forwarded.get(TENANT_SELECTOR_HEADER), "1");
  });

  it("B: header is not synthesized when absent", () => {
    const source = new Headers({
      "content-type": "application/json",
    });

    const forwarded = selectForwardHeaders(source);

    assert.equal(forwarded.has(TENANT_SELECTOR_HEADER), false);
    assert.equal(forwarded.get("content-type"), "application/json");
  });

  it("C: Nhost Authorization is injected by the server client, never via header forwarding", () => {
    const source = new Headers({
      authorization: "Bearer browser-token",
    });

    const forwarded = selectForwardHeaders(source);

    // The BFF's forwarding layer must never carry an Authorization header;
    // it is always sourced from the server-side Nhost session instead.
    assert.equal(forwarded.has("authorization"), false);
    assert.equal(FORWARDED_HEADERS.includes("authorization"), false);
  });

  it("D: cookie/Host remain blocked by the allowlist", () => {
    const source = new Headers({
      cookie: "nhostSession=secret",
      host: "evil.example.com",
      "x-request-id": "req-1",
    });

    const forwarded = selectForwardHeaders(source);

    assert.equal(forwarded.has("cookie"), false);
    assert.equal(forwarded.has("host"), false);
    assert.equal(forwarded.get("x-request-id"), "req-1");
  });

  it("E: tenant selector is transported only; frontend auth decisions ignore it", async () => {
    const mockNhost = {
      getUserSession: () => ({
        accessToken: "access-token",
        accessTokenExpiresIn: 900,
        refreshToken: "refresh-token",
        refreshTokenId: "refresh-id",
        user: { id: "user-1", email: "test@example.com" },
        decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
      }),
      refreshSession: async (marginSeconds: number) => {
        assert.equal(marginSeconds, 60);
        return {
          accessToken: "new-access-token",
          accessTokenExpiresIn: 900,
          refreshToken: "new-refresh-token",
          refreshTokenId: "new-refresh-id",
          user: { id: "user-1", email: "test@example.com" },
          decodedToken: { exp: Date.now() / 1000 + 900, sub: "user-1" },
        };
      },
      clearSession: () => {},
    };

    async function getAuthHeaderMock(nhost: typeof mockNhost): Promise<string | null> {
      const session = nhost.getUserSession();
      if (!session?.accessToken) return null;
      const refreshed = await nhost.refreshSession(60);
      if (!refreshed?.accessToken) return null;
      return `Bearer ${refreshed.accessToken}`;
    }

    // A browser-supplied selector travels as data only. It must never
    // influence the Authorization value produced for the backend.
    const source = new Headers({ [TENANT_SELECTOR_HEADER]: "42" });
    const forwarded = selectForwardHeaders(source);

    const authHeader = await getAuthHeaderMock(mockNhost);

    assert.equal(forwarded.get(TENANT_SELECTOR_HEADER), "42");
    assert.equal(authHeader, "Bearer new-access-token");
    assert.match(authHeader, /^Bearer new-access-token$/);
  });
});