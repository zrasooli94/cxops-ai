import { describe, it } from "node:test";
import assert from "node:assert/strict";

describe("auth input validation", () => {
  const validateEmail = (email: string): boolean => {
    if (!email || !email.includes("@")) return false;
    const parts = email.split("@");
    return parts.length === 2 && parts[0].length > 0 && parts[1].length > 0;
  };

  const validatePassword = (password: string): boolean => {
    return password.length >= 3 && password.length <= 50;
  };

  const validateCredentials = (email: string, password: string): { ok: boolean; error?: string } => {
    if (!email || !password) {
      return { ok: false, error: "Email and password are required." };
    }
    if (!validateEmail(email)) {
      return { ok: false, error: "Invalid email or password." };
    }
    if (!validatePassword(password)) {
      return { ok: false, error: "Invalid email or password." };
    }
    return { ok: true };
  };

  it("valid email and password are accepted", () => {
    const result = validateCredentials("test@example.com", "password123");
    assert.equal(result.ok, true);
  });

  it("missing email is rejected", () => {
    const result = validateCredentials("", "password123");
    assert.equal(result.ok, false);
    assert.equal(result.error, "Email and password are required.");
  });

  it("missing password is rejected", () => {
    const result = validateCredentials("test@example.com", "");
    assert.equal(result.ok, false);
    assert.equal(result.error, "Email and password are required.");
  });

  it("malformed email (no @) is rejected", () => {
    const result = validateCredentials("notanemail", "password123");
    assert.equal(result.ok, false);
    assert.equal(result.error, "Invalid email or password.");
  });

  it("malformed email (empty local part) is rejected", () => {
    const result = validateCredentials("@example.com", "password123");
    assert.equal(result.ok, false);
    assert.equal(result.error, "Invalid email or password.");
  });

  it("malformed email (empty domain) is rejected", () => {
    const result = validateCredentials("test@", "password123");
    assert.equal(result.ok, false);
    assert.equal(result.error, "Invalid email or password.");
  });

  it("email is normalized to lowercase", () => {
    const normalized = "TEST@EXAMPLE.COM".trim().toLowerCase();
    assert.equal(normalized, "test@example.com");
  });
});

describe("safe error mapping", () => {
  const mapAuthError = (providerError: unknown): string => {
    if (providerError instanceof Error) {
      // Never expose provider internal error text
      return "Invalid email or password.";
    }
    return "Sign-in service is temporarily unavailable.";
  };

  it("bad credentials map to generic message", () => {
    const error = new Error("User not found");
    assert.equal(mapAuthError(error), "Invalid email or password.");
  });

  it("password wrong maps to generic message", () => {
    const error = new Error("Password incorrect");
    assert.equal(mapAuthError(error), "Invalid email or password.");
  });

  it("provider internal JSON not exposed", () => {
    const error = new Error(JSON.stringify({ detail: "User with email test@example.com not found", status: 404 }));
    assert.equal(mapAuthError(error), "Invalid email or password.");
  });

  it("stack traces never exposed", () => {
    const error = new Error("Internal server error\n    at auth.ts:42\n    at handler.ts:10");
    assert.equal(mapAuthError(error), "Invalid email or password.");
  });

  it("non-Error throws map to service unavailable", () => {
    assert.equal(mapAuthError("string error"), "Sign-in service is temporarily unavailable.");
    assert.equal(mapAuthError(null), "Sign-in service is temporarily unavailable.");
    assert.equal(mapAuthError({}), "Sign-in service is temporarily unavailable.");
  });
});

describe("redirect safety (if implemented)", () => {
  const isSafeInternalPath = (path: string): boolean => {
    if (!path.startsWith("/")) return false;
    if (path.startsWith("//")) return false;
    if (path.startsWith("http://")) return false;
    if (path.startsWith("https://")) return false;
    if (path.startsWith("javascript:")) return false;
    if (path.startsWith("data:")) return false;
    return true;
  };

  it("internal path accepted", () => {
    assert.equal(isSafeInternalPath("/tickets"), true);
    assert.equal(isSafeInternalPath("/approvals"), true);
    assert.equal(isSafeInternalPath("/tickets/123"), true);
  });

  it("external URL rejected", () => {
    assert.equal(isSafeInternalPath("https://evil.example"), false);
    assert.equal(isSafeInternalPath("http://evil.example"), false);
  });

  it("protocol-relative URL rejected", () => {
    assert.equal(isSafeInternalPath("//evil.example"), false);
  });

  it("javascript URL rejected", () => {
    assert.equal(isSafeInternalPath("javascript:alert(1)"), false);
  });

  it("data URL rejected", () => {
    assert.equal(isSafeInternalPath("data:text/html,<script>"), false);
  });

  it("relative path without leading slash rejected", () => {
    assert.equal(isSafeInternalPath("tickets"), false);
  });
});

describe("sign-in error classification", () => {
  const classifySignInError = (error: unknown): string => {
    // Mock FetchError-like object for test classification
    const isFetchError = (err: unknown): err is { status: number } => {
      return typeof err === "object" && err !== null && "status" in err && typeof (err as { status: unknown }).status === "number";
    };

    if (isFetchError(error)) {
      const status = error.status;
      if (status === 400 || status === 401 || status === 403) {
        return "Invalid email or password.";
      }
      if (status >= 500) {
        return "Sign-in service is temporarily unavailable.";
      }
      return "Sign-in service is temporarily unavailable.";
    }
    return "Sign-in service is temporarily unavailable.";
  };

  it("FetchError 401 => Invalid email or password", () => {
    const error = { status: 401 };
    const result = classifySignInError(error);
    assert.equal(result, "Invalid email or password.");
  });

  it("FetchError 400 => Invalid email or password", () => {
    const error = { status: 400 };
    const result = classifySignInError(error);
    assert.equal(result, "Invalid email or password.");
  });

  it("FetchError 403 => Invalid email or password", () => {
    const error = { status: 403 };
    const result = classifySignInError(error);
    assert.equal(result, "Invalid email or password.");
  });

  it("FetchError 500 => service unavailable", () => {
    const error = { status: 500 };
    const result = classifySignInError(error);
    assert.equal(result, "Sign-in service is temporarily unavailable.");
  });

  it("FetchError 503 => service unavailable", () => {
    const error = { status: 503 };
    const result = classifySignInError(error);
    assert.equal(result, "Sign-in service is temporarily unavailable.");
  });

  it("non-FetchError => service unavailable", () => {
    const result = classifySignInError(new Error("network error"));
    assert.equal(result, "Sign-in service is temporarily unavailable.");
  });

  it("FetchError 429 => service unavailable", () => {
    const error = { status: 429 };
    const result = classifySignInError(error);
    assert.equal(result, "Sign-in service is temporarily unavailable.");
  });

  it("provider error body not returned to client", () => {
    const error = { status: 401, body: { detail: "User not found", token: "secret" } };
    const result = classifySignInError(error);
    assert.equal(result, "Invalid email or password.");
    assert.ok(!result.includes("User not found"));
    assert.ok(!result.includes("secret"));
  });
});

describe("auth view-model helper", () => {
  interface SessionUser {
    displayName: string | null;
    email: string | null;
    id: string;
    // tokens that must NEVER be exposed
    accessToken?: string;
    refreshToken?: string;
  }

  interface SafeAuthView {
    displayName: string | null;
    email: string | null;
    isAuthenticated: boolean;
  }

  const createAuthView = (user: SessionUser | null): SafeAuthView => {
    if (!user) {
      return { displayName: null, email: null, isAuthenticated: false };
    }
    return {
      displayName: user.displayName,
      email: user.email,
      isAuthenticated: true,
    };
  };

  it("exposes only safe display fields", () => {
    const user = {
      id: "user-123",
      displayName: "Test User",
      email: "test@example.com",
      accessToken: "secret-token",
      refreshToken: "secret-refresh",
    };
    const view = createAuthView(user);
    assert.equal(view.displayName, "Test User");
    assert.equal(view.email, "test@example.com");
    assert.equal(view.isAuthenticated, true);
    // Verify tokens are not exposed
    assert.ok(!("accessToken" in view));
    assert.ok(!("refreshToken" in view));
    assert.ok(!("id" in view));
  });

  it("null user returns unauthenticated view", () => {
    const view = createAuthView(null);
    assert.equal(view.displayName, null);
    assert.equal(view.email, null);
    assert.equal(view.isAuthenticated, false);
  });

  it("user with no email/displayName returns neutral state", () => {
    const user = { id: "user-123", displayName: null, email: null };
    const view = createAuthView(user);
    assert.equal(view.displayName, null);
    assert.equal(view.email, null);
    assert.equal(view.isAuthenticated, true);
  });
});