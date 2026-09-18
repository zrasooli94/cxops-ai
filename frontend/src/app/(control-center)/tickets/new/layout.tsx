import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: {
    absolute: "Create Test Support Case | CXOps AI",
  },

  description:
    "Create a demonstration support case for testing the CXOps AI RAG, risk-control, AI decision, and approval workflow.",

  alternates: {
    canonical:
      "/tickets/new",
  },

  robots: {
    index: false,
    follow: true,
  },
};

export default function RouteLayout({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/tickets/new"]}>
      {children}
    </CapabilityRouteGuard>
  );
}
