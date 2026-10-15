/** Staging-only identity and API targets. Never infer these from production. */
export const STAGING_AUTH_URL = "https://ep-tiny-dawn-azixl9yk.neonauth.c-3.ap-southeast-1.aws.neon.tech/cxops_staging/auth";
export const STAGING_BACKEND_URL = "https://cxops-ai-staging-bootstrap.onrender.com";

export function neonStagingEnabled(env: Readonly<Record<string, string | undefined>>): boolean {
  return env.VERCEL_ENV === "preview" &&
    env.CXOPS_PREVIEW_AUTH_ENABLED === "true" &&
    env.CXOPS_STAGING_ISOLATION_VERIFIED === "true" &&
    env.CXOPS_STAGING_AUTH_PROVIDER === "neon" &&
    stagingSubjects(env).length > 0 &&
    env.NEON_AUTH_BASE_URL === STAGING_AUTH_URL &&
    env.BACKEND_API_URL === STAGING_BACKEND_URL &&
    env.CXOPS_STAGING_BACKEND_URL === STAGING_BACKEND_URL &&
    (env.NEON_AUTH_COOKIE_SECRET?.length ?? 0) >= 32;
}

export function stagingSubjects(env: Readonly<Record<string, string | undefined>>): string[] {
  const values = (env.CXOPS_STAGING_ALLOWED_SUBJECTS ?? "").split(",").map(x => x.trim());
  return values.length <= 10 && values.every(x => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(x)) ? values : [];
}

export interface StagingSessionReader {
  getSession(): Promise<{ data?: { user?: { id?: string; name?: string | null; email?: string; emailVerified?: boolean } } | null; error?: unknown }>;
  token(): Promise<{ data?: { token?: string } | null; error?: unknown }>;
}

/** Read the official SDK session first. An arbitrary client bearer is never accepted. */
export async function stagingIdentity(reader: StagingSessionReader, allowedSubjects: readonly string[]) {
  const session = await reader.getSession();
  const user = session.data?.user;
  if (session.error || !user?.id || user.emailVerified !== true || !allowedSubjects.includes(user.id)) return null;
  const result = await reader.token();
  if (result.error || !result.data?.token) return null;
  return { user: { id: user.id, name: user.name ?? "Staging tester", email: user.email ?? "" }, token: result.data.token };
}
