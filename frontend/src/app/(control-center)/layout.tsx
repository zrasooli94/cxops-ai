import { Metadata } from "next";
import { redirect } from "next/navigation";
import { requireControlCenterAuth } from "@/lib/auth/control-center";
import {
  clearActiveOrganizationId,
  getActiveOrganizationId,
  setActiveOrganizationId,
} from "@/lib/tenant/cookie";
import { getMyOrganizations } from "@/lib/tenant/backend-client";
import ControlCenterShell from "./shell";

export const metadata: Metadata = {
  robots: {
    index: false,
    follow: false,
  },
};

interface TenantContext {
  activeOrganization: { id: number; name: string; industry: string | null };
  memberships: { id: number; name: string; industry: string | null }[];
}

async function resolveTenantContext(): Promise<TenantContext> {
  const activeOrganizationId = await getActiveOrganizationId();
  const memberships = await getMyOrganizations();

  if (memberships.length === 0) {
    redirect("/no-organization");
  }

  const membershipMap = new Map(memberships.map((m) => [m.id, m]));

  if (activeOrganizationId && membershipMap.has(activeOrganizationId)) {
    const active = membershipMap.get(activeOrganizationId)!;
    return {
      activeOrganization: active,
      memberships,
    };
  }

  if (memberships.length === 1) {
    const only = memberships[0];
    await setActiveOrganizationId(only.id);
    return {
      activeOrganization: only,
      memberships,
    };
  }

  // Multiple memberships and no valid selection: clear any stale cookie and
  // require explicit user selection.
  await clearActiveOrganizationId();
  redirect("/select-organization");
}

export default async function ControlCenterLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await requireControlCenterAuth();
  const tenantContext = await resolveTenantContext();

  return (
    <ControlCenterShell session={session} tenantContext={tenantContext}>
      {children}
    </ControlCenterShell>
  );
}
