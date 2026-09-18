import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "AI Operations Observability",

  description:
    "Explore CXOps AI operational observability for agent runs, approvals, model activity, RAG measurements, and execution outcomes.",

  alternates: {
    canonical:
      "/observability",
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
      requiredCapability={ROUTE_REQUIREMENTS["/observability"]}
    >
      {children}
    </CapabilityRouteGuard>
  );
}
