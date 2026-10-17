import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  stagingAgentEnrollmentTarget, stagingAgentEnrollmentViewer,
  stagingEnrollmentSetupCodeAccepted, stagingVerificationOtp, stagingAgentSignInTarget,
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

  it("requires an exact high-entropy temporary setup code for code issuance", () => {
    const code = "TEST_ONLY_high_entropy_staging_secret_123456";
    const env = { ...safePreview, CXOPS_STAGING_AGENT_ENROLL_CODE: code };
    assert.equal(stagingEnrollmentSetupCodeAccepted(env, code), true);
    for (const attempted of ["", "wrong", code + "x", 1234, null]) {
      assert.equal(stagingEnrollmentSetupCodeAccepted(env, attempted), false);
    }
    assert.equal(stagingEnrollmentSetupCodeAccepted(safePreview, code), false);
    assert.equal(stagingEnrollmentSetupCodeAccepted({
      ...safePreview, CXOPS_STAGING_AGENT_ENROLL_CODE: "short",
    }, "short"), false);
  });


  it("agent OTP sign-in remains separate, off by default and exact-subject bound", () => {
    const subject = "a8dd2946-2640-4122-b552-eca820d228de";
    const env = {
      ...safePreview,
      CXOPS_STAGING_AGENT_ENROLL_ENABLED: "false",
      CXOPS_STAGING_AGENT_SIGNIN_ENABLED: "true",
      CXOPS_STAGING_AGENT_ENROLL_CODE: "PRIVATE_TEST_ONLY_SETUP_CODE_NOT_REAL",
      CXOPS_STAGING_TICKET_AGENT_SUBJECT: subject,
      CXOPS_STAGING_ALLOWED_SUBJECTS: "4c09d849-2ec6-4caa-aeb8-91d631084e67," + subject,
    };
    assert.deepEqual(stagingAgentSignInTarget(env), {
      email: "enrolled.agent@gmail.com", subject,
    });
    for (const [key, value] of [
      ["VERCEL_ENV", "production"],
      ["CXOPS_STAGING_AGENT_SIGNIN_ENABLED", "false"],
      ["CXOPS_STAGING_AGENT_ENROLL_CODE", "short"],
      ["CXOPS_STAGING_AUTH_PROVIDER", "nhost"],
      ["CXOPS_STAGING_TICKET_AGENT_SUBJECT", "different"],
      ["CXOPS_STAGING_ALLOWED_SUBJECTS", "4c09d849-2ec6-4caa-aeb8-91d631084e67"],
      ["CXOPS_STAGING_AGENT_ENROLL_EMAIL", "user@example.test"],
    ] as const) {
      assert.equal(stagingAgentSignInTarget({ ...env, [key]: value }), null, key);
    }
    assert.equal(stagingAgentSignInTarget(safePreview), null);
  });

  it("never accepts a client-controlled email or malformed OTP", () => {
    assert.equal(stagingVerificationOtp(" 123456 "), "123456");
    for (const otp of [123456, "123", "1234567", "abcdef", "123 456", "", null]) {
      assert.equal(stagingVerificationOtp(otp), null);
    }
  });
});
