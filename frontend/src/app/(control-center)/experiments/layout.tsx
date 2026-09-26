import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Transformation Experiments",

  description:
    "Measure observed service outcomes against an immutable baseline, explicit targets, and optional simulation projections.",

  alternates: {
    canonical: "/experiments",
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
      requiredCapability={ROUTE_REQUIREMENTS["/experiments"]}
    >
      {children}
    </CapabilityRouteGuard>
  );
}