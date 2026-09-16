export const FORWARDED_HEADERS = [
  "content-type",
  "accept",
  "x-request-id",
] as const;

/**
 * The tenant selector is no longer forwarded from the browser. The BFF injects
 * it from the server-managed active-organization cookie instead. This prevents
 * a client-supplied header from overriding the trusted UX state.
 */
export const TENANT_SELECTOR_HEADER = "x-cxops-organization-id";

export type ForwardableHeader = (typeof FORWARDED_HEADERS)[number];

/**
 * Copy only the explicitly approved request headers over to the backend.
 *
 * The BFF injects the tenant selector (`x-cxops-organization-id`) from the
 * server-managed active-organization cookie. It is never interpreted or
 * authorized in Next.js: authorization remains exclusively with FastAPI's
 * CurrentTenant dependency, which validates the selector against the
 * authenticated subject's memberships.
 */
export function selectForwardHeaders(source: Headers): Headers {
  const selected = new Headers();

  for (const name of FORWARDED_HEADERS) {
    const value = source.get(name);
    if (value) {
      selected.set(name, value);
    }
  }

  return selected;
}

export interface BackendHeadersOptions {
  activeOrganizationId: number | null;
  authHeader: string | null;
  isDevelopment: boolean;
}

export interface BackendHeadersResult {
  headers: Headers;
  rejectUnauthorized: boolean;
}

/**
 * Build the headers sent to the FastAPI backend.
 *
 * - Only approved headers are forwarded; cookies and Host are never copied.
 * - The tenant selector header is injected from the server-managed cookie,
 *   ignoring any browser-provided value.
 * - Authorization comes from the server-side Nhost session in production.
 */
export function buildBackendHeaders(
  source: Headers,
  options: BackendHeadersOptions,
): BackendHeadersResult {
  const headers = selectForwardHeaders(source);
  let rejectUnauthorized = false;

  if (options.authHeader) {
    headers.set("authorization", options.authHeader);
  } else {
    if (!options.isDevelopment) {
      rejectUnauthorized = true;
    } else {
      const incomingAuth = source.get("authorization");
      if (incomingAuth) {
        headers.set("authorization", incomingAuth);
      }
    }
  }

  if (options.activeOrganizationId) {
    headers.set(
      TENANT_SELECTOR_HEADER,
      String(options.activeOrganizationId),
    );
  }

  return { headers, rejectUnauthorized };
}
