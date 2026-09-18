import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Customer 360 Workspace",

  description:
    "Explore the CXOps AI customer 360 workspace: customer search, service summary, linked tickets, and a unified activity timeline.",

  alternates: {
    canonical: "/customers",
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
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/customers"]}>
      {children}
    </CapabilityRouteGuard>
  );
}
