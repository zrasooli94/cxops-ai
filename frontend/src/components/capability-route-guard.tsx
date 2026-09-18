"use client";

import InsufficientPermission from "@/components/insufficient-permission";
import { useAuthorization } from "@/lib/authorization/context";
import type { Capability } from "@/lib/authorization/capabilities";
import { isCapabilityRequirementSatisfied } from "@/lib/authorization/navigation";

interface CapabilityRouteGuardProps {
  /**
   * Capability required to render the route, sourced from the shared
   * `ROUTE_REQUIREMENTS` map so navigation and protection cannot drift. A `null`
   * requirement means authenticated control-center access is enough.
   */
  requiredCapability: Capability | null;
  children: React.ReactNode;
}

/**
 * Client-side UX guard for a route segment.
 *
 * Rendered by each control-center route layout around its `children`. This is a
 * convenience layer only: FastAPI still authorizes every request, so a direct
 * navigation or a stale client view can never bypass the backend.
 */
export default function CapabilityRouteGuard({
  requiredCapability,
  children,
}: CapabilityRouteGuardProps) {
  const { can } = useAuthorization();

  if (!isCapabilityRequirementSatisfied(requiredCapability, can)) {
    return <InsufficientPermission />;
  }

  return <>{children}</>;
}
