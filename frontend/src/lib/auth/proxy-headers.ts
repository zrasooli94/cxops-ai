export const FORWARDED_HEADERS = [
  "content-type",
  "accept",
  "x-request-id",
  "x-cxops-organization-id",
] as const;

export const TENANT_SELECTOR_HEADER = "x-cxops-organization-id";

export type ForwardableHeader = (typeof FORWARDED_HEADERS)[number];

/**
 * Copy only the explicitly approved request headers over to the backend.
 *
 * The BFF transports the tenant selector (`x-cxops-organization-id`) to
 * FastAPI as data only. It is never interpreted or authorized in Next.js:
 * authorization remains exclusively with FastAPI's CurrentTenant dependency,
 * which validates the selector against the authenticated subject's memberships.
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