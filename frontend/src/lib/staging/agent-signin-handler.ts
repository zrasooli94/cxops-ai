import {
  stagingAgentSignInTarget, stagingEnrollmentSetupCodeAccepted, stagingVerificationOtp,
} from "./agent-enrollment.ts";
import { neonStagingEnabled, stagingSubjects } from "./neon-policy.ts";

const unavailable = "Verified staging-agent sign-in is not enabled.";
type Environment = Readonly<Record<string, string | undefined>>;

export async function requestAgentSignInOtp(
  env: Environment,
  form: FormData,
  sendOtp: (email: string) => Promise<{ error?: unknown }>,
): Promise<string | null> {
  if (!stagingEnrollmentSetupCodeAccepted(env, form.get("setup_code"))) {
    return "The private staging setup code was not accepted.";
  }
  const target = stagingAgentSignInTarget(env);
  if (!target || !neonStagingEnabled(env)) return unavailable;
  try {
    const result = await sendOtp(target.email);
    return result.error ? "Unable to request a code. Please wait before trying again." :
      "A private agent sign-in code was requested. Check your inbox.";
  } catch {
    return "The staging identity service is temporarily unavailable.";
  }
}

export async function verifyAgentSignInOtp(
  env: Environment,
  form: FormData,
  signIn: (email: string, otp: string) => Promise<{ error?: unknown }>,
  readIdentity: (allowedSubjects: readonly string[]) => Promise<{ user: { id: string } } | null>,
  clearSession: () => Promise<unknown>,
): Promise<{ completed: boolean; message: string | null }> {
  const target = stagingAgentSignInTarget(env);
  if (!target || !neonStagingEnabled(env)) return { completed: false, message: unavailable };
  const otp = stagingVerificationOtp(form.get("otp"));
  if (!otp) return { completed: false, message: "Enter the six-digit sign-in code." };
  try {
    const result = await signIn(target.email, otp);
    if (result.error) {
      return { completed: false, message: "Code not accepted or expired. Please request a fresh code." };
    }
    const identity = await readIdentity(stagingSubjects(env));
    if (identity?.user.id !== target.subject) {
      try { await clearSession(); } catch { /* Denial remains fail-closed. */ }
      return { completed: false, message: "The signed-in staging identity was not authorized." };
    }
    return { completed: true, message: null };
  } catch {
    return { completed: false, message: "Agent sign-in is temporarily unavailable." };
  }
}
