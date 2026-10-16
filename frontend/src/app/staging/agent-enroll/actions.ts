"use server";

import "server-only";

import {
  stagingAgentEnrollmentTarget, stagingAgentEnrollmentViewer,
  stagingEnrollmentSetupCodeAccepted, stagingVerificationOtp,
} from "@/lib/staging/agent-enrollment";
import {
  neonStagingEnabled, stagingIdentity, stagingSubjects,
} from "@/lib/staging/neon-policy";
import { stagingAuth } from "@/lib/staging/neon-server";

export type AgentEnrollmentResult = { outcome: "sent" | "verified" | "error"; message: string };

async function allowedAgentEnrollment() {
  const target = stagingAgentEnrollmentTarget(process.env);
  if (!target || !neonStagingEnabled(process.env)) return null;
  const auth = stagingAuth();
  const identity = await stagingIdentity(auth, stagingSubjects(process.env));
  if (!identity || !stagingAgentEnrollmentViewer(identity.user, stagingSubjects(process.env))) {
    return null;
  }
  return { auth, target };
}

/** Does not accept a client email, URL, JWT, mailbox setting, or SMTP secret. */
export async function sendAgentVerificationCode(
  _previous: AgentEnrollmentResult | null,
  form: FormData,
): Promise<AgentEnrollmentResult> {
  if (!stagingEnrollmentSetupCodeAccepted(process.env, form.get("setup_code"))) {
    return { outcome: "error", message: "Staging setup code was not accepted." };
  }
  try {
    const allowed = await allowedAgentEnrollment();
    if (!allowed) return { outcome: "error", message: "Agent enrollment is not available for this staging session." };
    // Neon Managed Better Auth's first-party Email OTP plugin, with a
    // server-fixed destination; the service owns code issuance and throttling.
    const result = await allowed.auth.emailOtp.sendVerificationOtp({
      email: allowed.target,
      type: "email-verification",
    });
    if (result.error) return { outcome: "error", message: "Could not send a code. Please try again later." };
    return { outcome: "sent", message: "Verification code requested. Check the staging-agent inbox." };
  } catch {
    return { outcome: "error", message: "Staging email verification is temporarily unavailable." };
  }
}

/** The OTP is consumed by Neon; it is never echoed, logged or persisted here. */
export async function confirmAgentVerificationCode(
  _previous: AgentEnrollmentResult | null,
  form: FormData,
): Promise<AgentEnrollmentResult> {
  const otp = stagingVerificationOtp(form.get("otp"));
  if (!otp) return { outcome: "error", message: "Enter the six-digit email verification code." };
  try {
    const allowed = await allowedAgentEnrollment();
    if (!allowed) return { outcome: "error", message: "Agent enrollment is not available for this staging session." };
    const result = await allowed.auth.emailOtp.verifyEmail({
      email: allowed.target,
      otp,
    });
    if (result.error) return { outcome: "error", message: "The code was not accepted. Check your inbox or request a new code." };
    return {
      outcome: "verified",
      message: "Neon accepted the verification code. Staff permissions remain disabled until a separate access review.",
    };
  } catch {
    return { outcome: "error", message: "Staging code verification is temporarily unavailable." };
  }
}
