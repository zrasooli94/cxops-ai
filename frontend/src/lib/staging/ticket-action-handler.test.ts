import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { STAGING_AUTH_URL, STAGING_BACKEND_URL } from "./neon-policy.ts";
import { stagingServerActionOriginAllowed } from "./server-action-security.ts";
import { runSyntheticTicketAction } from "./ticket-action-handler.ts";

const SUBJECT = "00000000-0000-4000-8000-000000001001";
const env = {
  VERCEL_ENV: "preview", CXOPS_PREVIEW_AUTH_ENABLED: "true",
  CXOPS_STAGING_ISOLATION_VERIFIED: "true", CXOPS_STAGING_AUTH_PROVIDER: "neon",
  CXOPS_STAGING_ALLOWED_SUBJECTS: SUBJECT, NEON_AUTH_BASE_URL: STAGING_AUTH_URL,
  BACKEND_API_URL: STAGING_BACKEND_URL, CXOPS_STAGING_BACKEND_URL: STAGING_BACKEND_URL,
  NEON_AUTH_COOKIE_SECRET: "x".repeat(32), CXOPS_STAGING_TICKET_ACTIONS_ENABLED: "true",
  CXOPS_STAGING_TICKET_AGENT_SUBJECT: SUBJECT,
};

function form(changes: Record<string, string> = {}) {
  const value = new FormData();
  for (const [key, item] of Object.entries({
    ticket_id: "7", operation: "release",
    expected_version: "2026-10-17T03:00:00.123456Z", ...changes,
  })) value.set(key, item);
  return value;
}

function response(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });
}

describe("Phase 1Y staging ticket Server Action", () => {
  it("uses only the fixed backend, exact tenant and versioned request", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const bodies = [
      [{ id: 42, name: "Synthetic Alpha Workshop" }],
      { organization_id: 42, role: "agent", capabilities: ["ticket.write"] },
      { id: 7 },
    ];
    const outcome = await runSyntheticTicketAction({
      env, form: form(), readIdentity: async () => ({ user: { id: SUBJECT }, token: "private-token" }),
      fetcher: async (input, init) => {
        calls.push({ url: String(input), init });
        return response(calls.length === 3 ? 200 : 200, bodies[calls.length - 1]);
      },
    });
    assert.equal(outcome, "completed");
    assert.deepEqual(calls.map(call => call.url), [
      STAGING_BACKEND_URL + "/me/organizations",
      STAGING_BACKEND_URL + "/me/authorization",
      STAGING_BACKEND_URL + "/staging/ticket-action",
    ]);
    assert.deepEqual(JSON.parse(String(calls[2].init?.body)), {
      ticket_id: 7, action: "release", expected_version: "2026-10-17T03:00:00.123456Z",
    });
    assert.equal((calls[2].init?.headers as Record<string, string>).Authorization,
      "Bearer private-token");
  });

  it("blocks missing, foreign and unverified identities before network access", async () => {
    for (const identity of [null, { user: { id: "foreign" }, token: "token" }]) {
      let calls = 0;
      const outcome = await runSyntheticTicketAction({
        env, form: form(), readIdentity: async () => identity,
        fetcher: async () => { calls += 1; return response(500, {}); },
      });
      assert.equal(outcome, "blocked");
      assert.equal(calls, 0);
    }
  });

  it("blocks Beta tenant selection and malformed authorization", async () => {
    for (const bodies of [
      [[{ id: 2, name: "Synthetic Beta Workshop" }]],
      [[{ id: 1, name: "Synthetic Alpha Workshop" }],
       { organization_id: 1, role: "viewer", capabilities: ["ticket.read"] }],
    ]) {
      let call = 0;
      const outcome = await runSyntheticTicketAction({
        env, form: form(), readIdentity: async () => ({ user: { id: SUBJECT }, token: "token" }),
        fetcher: async () => response(200, bodies[call++]),
      });
      assert.equal(outcome, "blocked");
      assert.ok(call <= 2);
    }
  });

  it("rejects stale or unauthorized form shapes and preserves backend conflicts", async () => {
    assert.equal(await runSyntheticTicketAction({
      env, form: form({ expected_version: "" }),
      readIdentity: async () => ({ user: { id: SUBJECT }, token: "token" }),
      fetcher: async () => { throw new Error("must not fetch"); },
    }), "blocked");
    let call = 0;
    assert.equal(await runSyntheticTicketAction({
      env, form: form(), readIdentity: async () => ({ user: { id: SUBJECT }, token: "token" }),
      fetcher: async () => [
        response(200, [{ id: 1, name: "Synthetic Alpha Workshop" }]),
        response(200, { organization_id: 1, role: "agent", capabilities: ["ticket.write"] }),
        response(409, {}),
      ][call++],
    }), "conflict");
  });

  it("rejects cross-origin and missing-origin submissions", () => {
    assert.equal(stagingServerActionOriginAllowed("https://preview.example", "preview.example"), true);
    assert.equal(stagingServerActionOriginAllowed("https://attacker.example", "preview.example"), false);
    assert.equal(stagingServerActionOriginAllowed(null, "preview.example"), false);
    assert.equal(stagingServerActionOriginAllowed("http://preview.example", "preview.example"), false);
  });
});
