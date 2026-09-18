import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Customer Support Ticket Workspace",

  description:
    "Explore the CXOps AI customer-support ticket workspace for RAG evidence, AI agent decisions, approvals, and controlled workflows.",

  alternates: {
    canonical:
      "/tickets",
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
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/tickets"]}>
      {children}
    </CapabilityRouteGuard>
  );
}
