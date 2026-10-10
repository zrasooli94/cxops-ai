import { Metadata } from "next";

import CapabilityRouteGuard from "@/components/capability-route-guard";
import { ROUTE_REQUIREMENTS } from "@/lib/authorization/navigation";

export const metadata: Metadata = {
  title: "Public Chat",
};

export default function PublicChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <CapabilityRouteGuard
      requiredCapability={ROUTE_REQUIREMENTS["/public-chat"]}
    >
      {children}
    </CapabilityRouteGuard>
  );
}