import test from "node:test";
import assert from "node:assert/strict";
import { neonStagingEnabled, stagingIdentity, STAGING_AUTH_URL, STAGING_BACKEND_URL } from "./neon-policy.ts";

const ready = { VERCEL_ENV: "preview", CXOPS_PREVIEW_AUTH_ENABLED: "true", CXOPS_STAGING_ISOLATION_VERIFIED: "true", CXOPS_STAGING_AUTH_PROVIDER: "neon", NEON_AUTH_BASE_URL: STAGING_AUTH_URL, BACKEND_API_URL: STAGING_BACKEND_URL, CXOPS_STAGING_BACKEND_URL: STAGING_BACKEND_URL, CXOPS_STAGING_ALLOWED_SUBJECTS: "11111111-1111-4111-8111-111111111111", NEON_AUTH_COOKIE_SECRET: "synthetic-test-cookie-secret-32-characters" };
test("staging requires every explicit setting", () => {
  assert.equal(neonStagingEnabled(ready), true);
  for (const key of Object.keys(ready)) assert.equal(neonStagingEnabled({ ...ready, [key]: undefined }), false, key);
});
for (const value of ["production", "development", "", undefined]) test(`Neon staging cannot run in ${value}`, () => {
  assert.equal(neonStagingEnabled({ ...ready, VERCEL_ENV: value }), false);
});
for (const key of ["BACKEND_API_URL", "CXOPS_STAGING_BACKEND_URL", "NEON_AUTH_BASE_URL"]) test(`${key} is pinned exactly`, () => {
  for (const value of ["https://cxops-ai.onrender.com", STAGING_AUTH_URL + "/", "http://localhost:3000", STAGING_BACKEND_URL + "@evil.example", "https://other.example"]) assert.equal(neonStagingEnabled({ ...ready, [key]: value }), false);
});
test("no token is requested for missing, rejected or unverified session", async () => {
  for (const session of [{ data: null }, { data: { user: { id: "synthetic" } } }, { error: "unavailable", data: { user: { id: "synthetic", emailVerified: true } } }]) {
    let calls = 0;
    assert.equal(await stagingIdentity({ getSession: async () => session, token: async () => { calls++; return { data: { token: "never" } }; } }, ["synthetic"]), null);
    assert.equal(calls, 0);
  }
});
test("SDK token errors fail closed", async () => {
  const getSession = async () => ({ data: { user: { id: "synthetic", emailVerified: true } } });
  assert.equal(await stagingIdentity({ getSession, token: async () => ({ error: "expired" }) }, ["synthetic"]), null);
  assert.equal(await stagingIdentity({ getSession, token: async () => ({ data: {} }) }, ["synthetic"]), null);
});
test("an unlisted verified user cannot request an API token", async () => {
  let calls = 0;
  const result = await stagingIdentity({ getSession: async () => ({ data: { user: { id: "outsider", emailVerified: true } } }), token: async () => { calls++; return { data: { token: "never" } }; } }, ["synthetic"]);
  assert.equal(result, null);
  assert.equal(calls, 0);
});
test("malformed enrollment and short secrets keep staging closed", () => {
  for (const value of ["", "*", "not-a-subject", ready.CXOPS_STAGING_ALLOWED_SUBJECTS + ","]) assert.equal(neonStagingEnabled({ ...ready, CXOPS_STAGING_ALLOWED_SUBJECTS: value }), false);
  assert.equal(neonStagingEnabled({ ...ready, NEON_AUTH_COOKIE_SECRET: "short" }), false);
});
test("verified SDK session supplies server-side API identity", async () => {
  const result = await stagingIdentity({ getSession: async () => ({ data: { user: { id: "synthetic", emailVerified: true } } }), token: async () => ({ data: { token: "synthetic-internal-only" } }) }, ["synthetic"]);
  assert.equal(result?.user.id, "synthetic");
  assert.equal(result?.token, "synthetic-internal-only");
});

test("Phase 1Y must never fall back to old Phase 1W API", () => {
  const earlierReadOnlyApi = "https://cxops-ai-staging-api.onrender.com";
  assert.notEqual(STAGING_BACKEND_URL, earlierReadOnlyApi);
  assert.equal(neonStagingEnabled({
    ...ready,
    BACKEND_API_URL: earlierReadOnlyApi,
    CXOPS_STAGING_BACKEND_URL: earlierReadOnlyApi,
  }), false);
});
