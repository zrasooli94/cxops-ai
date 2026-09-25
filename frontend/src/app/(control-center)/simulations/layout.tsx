import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Service Transformation Simulation",

  description:
    "Model operational what-if scenarios using observed service data and deterministic assumptions. Compare evaluated scenarios side-by-side and prepare executive decision summaries.",

  alternates: {
    canonical: "/simulations",
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
      requiredCapability={ROUTE_REQUIREMENTS["/simulations"]}
    >
      {children}
    </CapabilityRouteGuard>
  );
}