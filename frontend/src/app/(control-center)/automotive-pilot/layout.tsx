import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Automotive Pilot",

  description:
    "A1 Automotive Pilot showroom: the A1 demo tenant, its SLA policies, queues, knowledge pack, the 18-scenario evaluation catalog, and the safe optional replay path for the CXOps AI agent.",

  alternates: {
    canonical: "/automotive-pilot",
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
    <CapabilityRouteGuard
      requiredCapability={ROUTE_REQUIREMENTS["/automotive-pilot"]}
    >
      {children}
    </CapabilityRouteGuard>
  );
}