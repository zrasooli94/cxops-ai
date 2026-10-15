/**
 * Fail-closed preview access gate.
 *
 * Preview deployments currently share BACKEND_API_URL with production and do
 * not have an isolated Nhost instance. Public demo pages are unaffected.
 *
 * A preview may use authenticated staff endpoints only after an operator
 * explicitly attests that the separate staging backend, empty database, and
 * Nhost tenant were independently verified. The client cannot override this.
 *
 * This is one safeguard, not proof that an endpoint is backed by isolated data.
 */
export type PreviewGate = "not-preview" | "blocked" | "ready";

export type PreviewEnvironment = Readonly<Record<string, string | undefined>>;

const PRODUCTION_HOSTS = new Set([
  "cxops-ai.onrender.com",
  "cxops-ai.vercel.app",
]);

function safeRemoteOrigin(raw: string | undefined): string | null {
  if (!raw) return null;
  try {
    const value = new URL(raw);
    const host = value.hostname.toLowerCase();
    if (value.protocol !== "https:" || value.username || value.password) return null;
    if (value.search || value.hash) return null;
    if (value.pathname !== "/" && value.pathname !== "") return null;
    if (value.port && value.port !== "443") return null;
    if (!host || PRODUCTION_HOSTS.has(host)) return null;
    if (host === "localhost" || host === "127.0.0.1" || host === "::1" ||
        host.endsWith(".localhost") || host.endsWith(".local") ||
        host.endsWith(".internal") || host.startsWith("10.") ||
        host.startsWith("192.168.") || /^172\.(1[6-9]|2\d|3[01])\./.test(host)) return null;
    return value.origin;
  } catch {
    return null;
  }
}

export function previewGate(env: PreviewEnvironment): PreviewGate {
  if (env.VERCEL_ENV !== "preview") return "not-preview";

  // Both flags are server-side settings. They MUST remain absent/false until
  // separate staging infrastructure has passed its independent release gate.
  if (env.CXOPS_PREVIEW_AUTH_ENABLED !== "true" ||
      env.CXOPS_STAGING_ISOLATION_VERIFIED !== "true") return "blocked";

  const backend = safeRemoteOrigin(env.BACKEND_API_URL);
  const expectedBackend = safeRemoteOrigin(env.CXOPS_STAGING_BACKEND_URL);
  if (!backend || !expectedBackend || backend !== expectedBackend) return "blocked";

  // Require explicitly named staging identity; production's identifiers are
  // intentionally not copied into Preview. Matching strings do not, by
  // themselves, prove this identity project is separate from production.
  if (!env.NEXT_PUBLIC_NHOST_SUBDOMAIN ||
      !env.NEXT_PUBLIC_NHOST_REGION ||
      !env.CXOPS_STAGING_NHOST_SUBDOMAIN ||
      !env.CXOPS_STAGING_NHOST_REGION ||
      env.NEXT_PUBLIC_NHOST_SUBDOMAIN !== env.CXOPS_STAGING_NHOST_SUBDOMAIN ||
      env.NEXT_PUBLIC_NHOST_REGION !== env.CXOPS_STAGING_NHOST_REGION) return "blocked";

  return "ready";
}

export function previewGateFromEnvironment(): PreviewGate {
  return previewGate(process.env);
}
