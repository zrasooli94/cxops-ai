import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Agent Runs & Audit Trail",

  description:
    "Explore the CXOps AI audit trail for agent runs, decisions, approvals, execution status, and recorded workflow outcomes.",

  alternates: {
    canonical:
      "/runs",
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
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/runs"]}>
      {children}
    </CapabilityRouteGuard>
  );
}
