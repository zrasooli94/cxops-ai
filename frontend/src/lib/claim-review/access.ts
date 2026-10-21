/**
 * Pure authorization predicate for the C8 staff-only, synthetic local review.
 *
 * Unlike a role-name comparison, this uses the backend-authenticated
 * organization/capability snapshot and requires BOTH read + manage grants.
 * It is used before rendering the client component; FastAPI is still the
 * authority for all actual organization/tenant API requests.
 *
 * No reviewer API or persistent mutation exists in this milestone.
 */
import { CAPABILITIES } from "../authorization/capabilities.ts";
import type { AuthorizationInfo } from "../authorization/types.ts";

export function mayUseClaimReview(
  authorization: Pick<AuthorizationInfo, "organization_id" | "capabilities">,
  activeOrganizationId: number | null,
): boolean {
  return (
    activeOrganizationId !== null &&
    Number.isSafeInteger(activeOrganizationId) &&
    activeOrganizationId > 0 &&
    authorization.organization_id === activeOrganizationId &&
    authorization.capabilities.includes(CAPABILITIES.EVALUATION_READ) &&
    authorization.capabilities.includes(CAPABILITIES.EVALUATION_MANAGE)
  );
}
