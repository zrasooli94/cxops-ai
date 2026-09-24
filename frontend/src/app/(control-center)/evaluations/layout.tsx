import type { Metadata } from "next";
import type { ReactNode } from "react";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "AI Evaluations",

  description:
    "Inspect CXOps AI evaluation runs and case results across RAG and Agent targets.",

  alternates: {
    canonical: "/evaluations",
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
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/evaluations"]}>
      {children}
    </CapabilityRouteGuard>
  );
}