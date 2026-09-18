import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Human Approval Queue",

  description:
    "Explore the CXOps AI human approval workflow for reviewing AI-assisted support actions before controlled execution.",

  alternates: {
    canonical:
      "/approvals",
  },

  robots: {
    index: true,
    follow: true,
  },
};

export default function RouteLayout({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/approvals"]}>
      {children}
    </CapabilityRouteGuard>
  );
}
