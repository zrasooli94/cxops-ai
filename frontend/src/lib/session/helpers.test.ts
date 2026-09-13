import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  serializeSession,
  deserializeSession,
  getAccessToken,
  hasValidSession,
} from "./helpers.ts";
import type { StoredSession } from "@nhost/nhost-js/session";

const validSession: StoredSession = {
  accessToken: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
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

describe("session helpers", () => {
  it("serializes a valid session to JSON", () => {
    const json = serializeSession(validSession);
    assert.equal(typeof json, "string");
    const parsed = JSON.parse(json);
    assert.equal(parsed.accessToken, validSession.accessToken);
  });

  it("deserializes valid JSON back to session", () => {
    const json = serializeSession(validSession);
    const session = deserializeSession(json);
    assert.ok(session !== null);
    assert.equal(session?.accessToken, validSession.accessToken);
  });

  it("returns null for null input", () => {
    assert.equal(deserializeSession(null), null);
  });

  it("returns null for empty string", () => {
    assert.equal(deserializeSession(""), null);
  });

  it("returns null for invalid JSON", () => {
    assert.equal(deserializeSession("not-json"), null);
  });

  it("returns null for object missing required fields", () => {
    assert.equal(deserializeSession(JSON.stringify({ accessToken: "x" })), null);
    assert.equal(deserializeSession(JSON.stringify({})), null);
  });

  it("extracts access token from valid session", () => {
    assert.equal(getAccessToken(validSession), validSession.accessToken);
  });

  it("returns null for missing session", () => {
    assert.equal(getAccessToken(null), null);
  });

  it("validates a complete session", () => {
    assert.equal(hasValidSession(validSession), true);
  });

  it("rejects session missing access token", () => {
    const incomplete = { ...validSession, accessToken: "" };
    assert.equal(hasValidSession(incomplete), false);
  });

  it("rejects session missing refresh token", () => {
    const incomplete = { ...validSession, refreshToken: "" };
    assert.equal(hasValidSession(incomplete), false);
  });

  it("rejects session missing user", () => {
    const incomplete = { ...validSession, user: undefined };
    assert.equal(hasValidSession(incomplete), false);
  });

  it("rejects null", () => {
    assert.equal(hasValidSession(null), false);
  });
});