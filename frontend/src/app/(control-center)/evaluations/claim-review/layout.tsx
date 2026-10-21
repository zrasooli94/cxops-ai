import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Claim Review | CXOps AI",
  description: "Staff-only, synthetic claim-evidence review workspace.",
  robots: { index: false, follow: false },
};

export default function ClaimReviewLayout({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <CapabilityRouteGuard
      requiredCapability={ROUTE_REQUIREMENTS["/evaluations/claim-review"]}
    >
      {children}
    </CapabilityRouteGuard>
  );
}
