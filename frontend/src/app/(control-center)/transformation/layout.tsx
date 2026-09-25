import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Service Transformation",

  description:
    "Explore CXOps AI service transformation analytics for ticket volume, SLA health, AI adoption, specialist usage, human workload, and value realization over 7, 30, and 90 day windows.",

  alternates: {
    canonical:
      "/transformation",
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
      requiredCapability={ROUTE_REQUIREMENTS["/transformation"]}
    >
      {children}
    </CapabilityRouteGuard>
  );
}