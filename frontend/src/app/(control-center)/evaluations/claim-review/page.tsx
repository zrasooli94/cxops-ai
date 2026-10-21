import AuthorizationUnavailable from "@/components/authorization-unavailable";
import type { AuthorizationInfo } from "@/lib/authorization/types";
import InsufficientPermission from "@/components/insufficient-permission";
import { mayUseClaimReview } from "@/lib/claim-review/access";
import { getActiveOrganizationId } from "@/lib/tenant/cookie";
import { getMyAuthorizationReadOnly } from "@/lib/tenant/backend-client";

import ClaimReviewWorkspace from "./review-workspace";

// Both authentication and tenant membership are resolved by the enclosing
// control-center server layout. THIS route adds a second, read-only server
// capability check for evaluation.read + evaluation.manage on that SAME
// organization before sending ANY review UI to the browser.
export const dynamic = "force-dynamic";

export default async function ClaimReviewPage() {
  const activeOrganizationId = await getActiveOrganizationId();
  if (activeOrganizationId === null) {
    return <AuthorizationUnavailable />;
  }
  let authorization: AuthorizationInfo | null = null;
  try {
    // FastAPI validates membership and organization selector. Never derive
    // a grant from the URL, a role name, an input CSV or an untrusted claim.
    authorization = await getMyAuthorizationReadOnly(activeOrganizationId);
  } catch {
    // No exception text, membership metadata, or source body is exposed.
    // JSX is returned OUTSIDE try/catch per React compiler rules.
  }
  if (authorization === null) return <AuthorizationUnavailable />;
  if (!mayUseClaimReview(authorization, activeOrganizationId)) {
    return <InsufficientPermission />;
  }
  // C12 is source-only: preview must be explicitly enabled on the Next
  // development server AND on the C11 FastAPI development backend. Never
  // expose reviewer writes in production, even if a flag is misconfigured.
  const previewEnabled =
    process.env.NODE_ENV !== "production" &&
    process.env.CXOPS_C12_SYNTHETIC_REVIEW_PREVIEW === "true";
  return (
    <ClaimReviewWorkspace
      key={activeOrganizationId}
      authorizedOrganizationId={activeOrganizationId}
      previewEnabled={previewEnabled}
    />
  );
}
