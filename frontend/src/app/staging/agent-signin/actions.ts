"use server";

import "server-only";

import { redirect } from "next/navigation";
import {
  stagingAgentSignInTarget, stagingEnrollmentSetupCodeAccepted, stagingVerificationOtp,
} from "@/lib/staging/agent-enrollment";
import { neonStagingEnabled } from "@/lib/staging/neon-policy";
import { stagingAuth } from "@/lib/staging/neon-server";

const unavailable = "Verified staging-agent sign-in is not enabled.";

export async function requestStagingAgentSignInOtp(
  _previous: string | null, form: FormData,
): Promise<string | null> {
  if (!stagingEnrollmentSetupCodeAccepted(process.env, form.get("setup_code"))) {
    return "The private staging setup code was not accepted.";
  }
  const target = stagingAgentSignInTarget(process.env);
  if (!target || !neonStagingEnabled(process.env)) return unavailable;
  try {
    const result = await stagingAuth().emailOtp.sendVerificationOtp({
      email: target.email, type: "sign-in",
    });
    if (result.error) return "Unable to request a code. Please wait before trying again.";
    return "A private agent sign-in code was requested. Check your inbox.";
  } catch {
    return "The staging identity service is temporarily unavailable.";
  }
}

export async function verifyStagingAgentSignInOtp(
  _previous: string | null, form: FormData,
): Promise<string | null> {
  const target = stagingAgentSignInTarget(process.env);
  if (!target || !neonStagingEnabled(process.env)) return unavailable;
  const otp = stagingVerificationOtp(form.get("otp"));
  if (!otp) return "Enter the six-digit sign-in code.";
  try {
    // The Neon session is only an identity. Every staff read/write is still
    // restricted by verified subject + per-tenant backend RBAC and release flags.
    const result = await stagingAuth().signIn.emailOtp({ email: target.email, otp });
    if (result.error) return "Code not accepted or expired. Please request a fresh code.";
  } catch {
    return "Agent sign-in is temporarily unavailable.";
  }
  redirect("/staging");
}
