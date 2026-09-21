import type { Metadata } from "next";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Unified Conversation Inbox",
  description: "Cross-channel conversation inbox for customer operations.",
  alternates: { canonical: "/inbox" },
  robots: { index: true, follow: true },
};

export default function RouteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/inbox"]}>
      {children}
    </CapabilityRouteGuard>
  );
}