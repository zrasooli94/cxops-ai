import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  stagingAgentEnrollmentTarget, stagingAgentEnrollmentViewer, stagingVerificationOtp,
} from "./agent-enrollment.ts";

const safePreview = {
  VERCEL_ENV: "preview",
  CXOPS_STAGING_AGENT_ENROLL_ENABLED: "true",
  CXOPS_PREVIEW_AUTH_ENABLED: "true",
  CXOPS_STAGING_ISOLATION_VERIFIED: "true",
  CXOPS_STAGING_AUTH_PROVIDER: "neon",
  CXOPS_STAGING_AGENT_ENROLL_EMAIL: "enrolled.agent@gmail.com",
};

describe("synthetic agent enrollment policy", () => {
  it("rejects production, disabled staging and wrong provider", () => {
    assert.equal(stagingAgentEnrollmentTarget(safePreview), "enrolled.agent@gmail.com");
    for (const [key, value] of [
      ["VERCEL_ENV", "production"],
      ["CXOPS_STAGING_AGENT_ENROLL_ENABLED", "false"],
      ["CXOPS_PREVIEW_AUTH_ENABLED", "false"],
      ["CXOPS_STAGING_ISOLATION_VERIFIED", "false"],
      ["CXOPS_STAGING_AUTH_PROVIDER", "nhost"],
    ] as const) {
      assert.equal(stagingAgentEnrollmentTarget({ ...safePreview, [key]: value }), null);
    }
  });

  it("rejects missing, undeliverable and malformed target emails", () => {
    for (const email of ["", "not-an-email", "user@example.test", "a".repeat(260), "space @gmail.com"]) {
      assert.equal(stagingAgentEnrollmentTarget({
        ...safePreview, CXOPS_STAGING_AGENT_ENROLL_EMAIL: email,
      }), null);
    }
  });

  it("only the existing verified Alpha viewer may initiate the flow", () => {
    const allowed = ["alpha-subject", "beta-subject"];
    assert.equal(stagingAgentEnrollmentViewer({
      id: "alpha-subject", email: "phase1w-alpha@example.test",
    }, allowed), true);
    for (const viewer of [
      null, { id: "alpha-subject", email: "phase1w-beta@example.test" },
      { id: "beta-subject", email: "phase1w-beta@example.test" },
      { id: "fake-subject", email: "phase1w-alpha@example.test" },
    ]) assert.equal(stagingAgentEnrollmentViewer(viewer, allowed), false);
  });

  it("never accepts a client-controlled email or malformed OTP", () => {
    assert.equal(stagingVerificationOtp(" 123456 "), "123456");
    for (const otp of [123456, "123", "1234567", "abcdef", "123 456", "", null]) {
      assert.equal(stagingVerificationOtp(otp), null);
    }
  });
});
