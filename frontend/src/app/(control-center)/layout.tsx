import { Metadata } from "next";
import { redirect } from "next/navigation";
import AuthorizationUnavailable from "@/components/authorization-unavailable";
import { requireControlCenterAuth } from "@/lib/auth/control-center";
import { AuthorizationProvider } from "@/lib/authorization/context";
import {
  AuthorizationLoadError,
  type AuthorizationLoadFailure,
} from "@/lib/authorization/helpers";
import type { AuthorizationInfo } from "@/lib/authorization/types";
import {
  getMyAuthorization,
  getMyOrganizations,
} from "@/lib/tenant/backend-client";
import {
  clearActiveOrganizationId,
  getActiveOrganizationId,
  setActiveOrganizationId,
} from "@/lib/tenant/cookie";
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

type AuthorizationResult =
  | { status: "ok"; authorization: AuthorizationInfo }
  | { status: "error" };

function classificationOf(error: unknown): AuthorizationLoadFailure | "unknown" {
  if (error instanceof AuthorizationLoadError) {
    return error.reason;
  }
  // The server backend client throws this before the loader runs when the
  // session cannot be refreshed.
  if (error instanceof Error && error.message === "Authentication required") {
    return "authentication";
  }
  return "unknown";
}

/**
 * Load authorization for the already-resolved organization.
 *
 * Order is deliberate: identity, then tenant resolution, then authorization.
 * The organization id always comes from the tenant flow — never from the
 * authorization response. Auth failures reuse the login redirect; membership
 * failures reuse the organization recovery flow; malformed/mismatched/
 * unavailable responses fail closed without redirecting.
 */
async function loadAuthorizationOrRecover(
  organizationId: number,
): Promise<AuthorizationResult> {
  try {
    const authorization = await getMyAuthorization(organizationId);
    return { status: "ok", authorization };
  } catch (error) {
    const classification = classificationOf(error);

    if (classification === "authentication") {
      redirect("/login");
    }

    if (classification === "membership") {
      await clearActiveOrganizationId();
      redirect("/select-organization");
    }

    return { status: "error" };
  }
}

export default async function ControlCenterLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await requireControlCenterAuth();
  const tenantContext = await resolveTenantContext();
  const result = await loadAuthorizationOrRecover(
    tenantContext.activeOrganization.id,
  );

  if (result.status === "error") {
    return <AuthorizationUnavailable />;
  }

  const { authorization } = result;

  return (
    <AuthorizationProvider
      key={authorization.organization_id}
      authorization={authorization}
    >
      <ControlCenterShell session={session} tenantContext={tenantContext}>
        {children}
      </ControlCenterShell>
    </AuthorizationProvider>
  );
}
