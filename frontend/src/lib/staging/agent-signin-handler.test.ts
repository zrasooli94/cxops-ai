import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { requestAgentSignInOtp, verifyAgentSignInOtp } from "./agent-signin-handler.ts";
import { STAGING_AUTH_URL, STAGING_BACKEND_URL } from "./neon-policy.ts";

const SUBJECT = "00000000-0000-4000-8000-000000001001";
const EMAIL = "agent@example.org";
const SETUP = "a-private-setup-code-longer-than-24";
const env = {
  VERCEL_ENV: "preview", CXOPS_PREVIEW_AUTH_ENABLED: "true",
  CXOPS_STAGING_ISOLATION_VERIFIED: "true", CXOPS_STAGING_AUTH_PROVIDER: "neon",
  CXOPS_STAGING_ALLOWED_SUBJECTS: SUBJECT, NEON_AUTH_BASE_URL: STAGING_AUTH_URL,
  BACKEND_API_URL: STAGING_BACKEND_URL, CXOPS_STAGING_BACKEND_URL: STAGING_BACKEND_URL,
  NEON_AUTH_COOKIE_SECRET: "x".repeat(32), CXOPS_STAGING_AGENT_SIGNIN_ENABLED: "true",
  CXOPS_STAGING_TICKET_AGENT_SUBJECT: SUBJECT,
  CXOPS_STAGING_AGENT_ENROLL_EMAIL: EMAIL, CXOPS_STAGING_AGENT_ENROLL_CODE: SETUP,
};

describe("Phase 1Y staging agent OTP Server Action", () => {
  it("sends only to the server-fixed mailbox after the setup gate", async () => {
    const form = new FormData();
    form.set("setup_code", SETUP);
    form.set("email", "attacker@example.net");
    const sent: string[] = [];
    const message = await requestAgentSignInOtp(env, form, async email => {
      sent.push(email); return {};
    });
    assert.deepEqual(sent, [EMAIL]);
    assert.match(String(message), /requested/);
  });

  it("does not call Neon for an invalid setup code", async () => {
    const form = new FormData();
    form.set("setup_code", "wrong");
    let called = false;
    await requestAgentSignInOtp(env, form, async () => { called = true; return {}; });
    assert.equal(called, false);
  });

  it("requires the post-OTP session to resolve to the exact agent subject", async () => {
    const form = new FormData();
    form.set("otp", "123456");
    let cleared = 0;
    for (const identity of [null, { user: { id: "foreign-subject" } }]) {
      const result = await verifyAgentSignInOtp(
        env, form, async (email, otp) => {
          assert.equal(email, EMAIL); assert.equal(otp, "123456"); return {};
        }, async () => identity, async () => { cleared += 1; },
      );
      assert.equal(result.completed, false);
      assert.match(String(result.message), /not authorized/);
    }
    assert.equal(cleared, 2);
    const accepted = await verifyAgentSignInOtp(
      env, form, async () => ({}), async () => ({ user: { id: SUBJECT } }),
      async () => { throw new Error("must not clear accepted session"); },
    );
    assert.deepEqual(accepted, { completed: true, message: null });
  });

  it("rejects malformed OTPs and provider failures without identity reads", async () => {
    const malformed = new FormData();
    malformed.set("otp", "12345x");
    let calls = 0;
    const invalid = await verifyAgentSignInOtp(
      env, malformed, async () => { calls += 1; return {}; },
      async () => { calls += 1; return { user: { id: SUBJECT } }; },
      async () => { calls += 1; },
    );
    assert.equal(invalid.completed, false);
    assert.equal(calls, 0);
    const valid = new FormData(); valid.set("otp", "123456");
    const denied = await verifyAgentSignInOtp(
      env, valid, async () => ({ error: new Error("denied") }),
      async () => { throw new Error("must not read session"); },
      async () => { throw new Error("must not clear absent session"); },
    );
    assert.equal(denied.completed, false);
  });
});
