import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "AI Agent Console",

  description:
    "Explore the CXOps AI agent workflow for RAG-assisted support reasoning, risk controls, human approvals, and auditable execution.",

  alternates: {
    canonical:
      "/agent",
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
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/agent"]}>
      {children}
    </CapabilityRouteGuard>
  );
}
