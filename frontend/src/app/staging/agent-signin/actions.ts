"use server";

import "server-only";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { requestAgentSignInOtp, verifyAgentSignInOtp } from "@/lib/staging/agent-signin-handler";
import { stagingIdentity } from "@/lib/staging/neon-policy";
import { stagingAuth } from "@/lib/staging/neon-server";
import { stagingServerActionOriginAllowed } from "@/lib/staging/server-action-security";

const unavailable = "Verified staging-agent sign-in is not enabled.";

export async function requestStagingAgentSignInOtp(
  _previous: string | null, form: FormData,
): Promise<string | null> {
  const requestHeaders = await headers();
  if (!stagingServerActionOriginAllowed(
    requestHeaders.get("origin"), requestHeaders.get("host"),
  )) return unavailable;
  return requestAgentSignInOtp(process.env, form, email =>
    stagingAuth().emailOtp.sendVerificationOtp({ email, type: "sign-in" }));
}

export async function verifyStagingAgentSignInOtp(
  _previous: string | null, form: FormData,
): Promise<string | null> {
  const requestHeaders = await headers();
  if (!stagingServerActionOriginAllowed(
    requestHeaders.get("origin"), requestHeaders.get("host"),
  )) return unavailable;
  const auth = stagingAuth();
  const result = await verifyAgentSignInOtp(
    process.env,
    form,
    (email, otp) => auth.signIn.emailOtp({ email, otp }),
    allowed => stagingIdentity(auth, allowed),
    () => auth.signOut(),
  );
  if (!result.completed) return result.message;
  redirect("/staging");
}
