import type { Metadata } from "next";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Setup",
  description: "Guided setup and readiness checklist for your workspace.",
  robots: { index: false, follow: false },
};

export default function RouteLayout({ children }: { children: React.ReactNode }) {
  return (
    <CapabilityRouteGuard requiredCapability={ROUTE_REQUIREMENTS["/setup"]}>
      {children}
    </CapabilityRouteGuard>
  );
}
