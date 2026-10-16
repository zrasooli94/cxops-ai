/** Server-side policy for onboarding a separately verified staging agent.
 * All inputs are untrusted until matched against the pinned staging policy.
 * No email is taken from a form or client-side query string.
 */
export type StagingEnrollmentEnvironment = Readonly<Record<string, string | undefined>>;

export function stagingAgentEnrollmentTarget(env: StagingEnrollmentEnvironment): string | null {
  if (env.VERCEL_ENV !== "preview" ||
      env.CXOPS_STAGING_AGENT_ENROLL_ENABLED !== "true" ||
      env.CXOPS_PREVIEW_AUTH_ENABLED !== "true" ||
      env.CXOPS_STAGING_ISOLATION_VERIFIED !== "true" ||
      env.CXOPS_STAGING_AUTH_PROVIDER !== "neon") return null;
  const email = (env.CXOPS_STAGING_AGENT_ENROLL_EMAIL ?? "").trim().toLowerCase();
  // The email is set by a branch-scoped, server-only Vercel variable,
  // not submitted by the browser. A real mailbox is required for OTP.
  if (email.length > 254 || !/^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$/.test(email) ||
      email.endsWith("@example.test")) return null;
  return email;
}

export function stagingAgentEnrollmentViewer(
  viewer: { email?: string; id?: string } | null,
  allowedSubjects: readonly string[],
): boolean {
  return Boolean(viewer?.id &&
    allowedSubjects.includes(viewer.id) &&
    viewer.email === "phase1w-alpha@example.test");
}

export function stagingVerificationOtp(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const otp = value.trim();
  return /^\d{6}$/.test(otp) ? otp : null;
}
