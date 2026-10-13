import type { Metadata } from "next";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Overview",
  description: "Your business at a glance: chatbot status, conversations, usage and team.",
  robots: { index: false, follow: false },
};

export default function RouteLayout({ children }: { children: React.ReactNode }) {
  return (
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/workspace"]}>
      {children}
    </CapabilityRouteGuard>
  );
}
