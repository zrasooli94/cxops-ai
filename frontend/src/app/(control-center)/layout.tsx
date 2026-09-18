import { Metadata } from "next";
import { redirect } from "next/navigation";
import AuthorizationUnavailable from "@/components/authorization-unavailable";
import {
  readControlCenterSession,
  requireControlCenterAuth,
} from "@/lib/auth/control-center";
import { AuthorizationProvider } from "@/lib/authorization/context";
import {
  AuthorizationLoadError,
  type AuthorizationLoadFailure,
} from "@/lib/authorization/helpers";
import type { AuthorizationInfo } from "@/lib/authorization/types";
import {
  getMyAuthorizationReadOnly,
  getMyOrganizationsReadOnly,
} from "@/lib/tenant/backend-client";
import { getActiveOrganizationId } from "@/lib/tenant/cookie";
import { controlCenterBootstrap } from "@/lib/session/read";
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

type AuthorizationResult =
  | { status: "ok"; authorization: AuthorizationInfo }
  | { status: "error" };

function classificationOf(error: unknown): AuthorizationLoadFailure | "unknown" {
  if (error instanceof AuthorizationLoadError) {
    return error.reason;
  }
  return "unknown";
}

/**
 * Load authorization for the already-resolved organization through the
 * READ-ONLY path (cookie access token, no refresh, no cookie writes).
 *
 * Auth failures route to the session-recovery Route Handler; membership
 * failures route to the landing Route Handler which owns persisting/clearing
 * the tenant-selection cookie; everything else fails closed without redirect.
 */
async function loadAuthorizationOrRecover(
  organizationId: number,
): Promise<AuthorizationResult> {
  try {
    const authorization = await getMyAuthorizationReadOnly(organizationId);
    return { status: "ok", authorization };
  } catch (error) {
    const classification = classificationOf(error);

    if (classification === "authentication") {
      redirect("/api/auth/session");
    }

    if (classification === "membership") {
      redirect("/api/auth/landing");
    }

    return { status: "error" };
  }
}

/**
 * Control-center layout.
 *
 * READ-ONLY by design: Server Component rendering never sets, deletes, or
 * refreshes cookies (Next.js throws on cookie mutation outside a Server Action
 * or Route Handler). The only cookie writes in a control-center request happen
 * in Route Handlers:
 * - a stale session redirects to `/api/auth/session` (one refresh, clears on
 *   failure) - it can never 500 this render;
 * - a single membership or an invalid/absent tenant selection redirects to
 *   `/api/auth/landing`, which persists/clears the org cookie and redirects
 *   back to the right destination (handles deep links).
 */
export default async function ControlCenterLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { state } = await readControlCenterSession();
  const session = await requireControlCenterAuth();

  const activeOrganizationId = await getActiveOrganizationId();

  let memberships;
  try {
    memberships = await getMyOrganizationsReadOnly();
  } catch (error) {
    if (classificationOf(error) === "authentication") {
      redirect("/api/auth/session");
    }
    return <AuthorizationUnavailable />;
  }

  const bootstrap = controlCenterBootstrap(
    state,
    memberships.map((m) => ({ id: m.id })),
    activeOrganizationId,
  );

  if (bootstrap === "no-organization") {
    redirect("/no-organization");
  }
  if (bootstrap === "landing") {
    redirect("/api/auth/landing");
  }

  const selected =
    activeOrganizationId !== null
      ? memberships.find((m) => m.id === activeOrganizationId)
      : undefined;

  if (!selected) {
    // Control-center membership resolution must be reachable even when the
    // cookie read and membership read disagree; fail closed.
    redirect("/api/auth/landing");
  }

  const result = await loadAuthorizationOrRecover(selected.id);

  if (result.status === "error") {
    return <AuthorizationUnavailable />;
  }

  const { authorization } = result;
  const tenantContext: TenantContext = {
    activeOrganization: {
      id: selected.id,
      name: selected.name,
      industry: selected.industry,
    },
    memberships: memberships.map((m) => ({
      id: m.id,
      name: m.name,
      industry: m.industry,
    })),
  };

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