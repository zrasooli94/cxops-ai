export const ACTIVE_ORGANIZATION_COOKIE_NAME = "cxopsOrganization";

/**
 * Parse a raw cookie value into a positive organization id.
 *
 * Malformed or non-positive values are treated as absent so a tampered cookie
 * cannot crash rendering or be mistaken for a valid selector.
 */
export function parseActiveOrganizationId(
  raw: string | undefined,
): number | null {
  if (!raw) {
    return null;
  }
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return null;
  }
  return parsed;
}
