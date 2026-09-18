"use client";

import {
  createContext,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";

import type { Capability } from "@/lib/authorization/capabilities";
import { createAuthorizationView } from "@/lib/authorization/helpers";
import type { AuthorizationInfo } from "@/lib/authorization/types";

/**
 * Client-side authorization surface.
 *
 * This is UX state only. Every decision here is derived from the capability set
 * the server received from FastAPI; `role` is display information. The backend
 * re-checks every request, so hiding or disabling a control is never a security
 * boundary.
 */
export interface AuthorizationValue {
  organizationId: number;
  role: string;
  capabilities: readonly string[];
  can: (capability: Capability) => boolean;
  canAny: (capabilities: readonly Capability[]) => boolean;
  canAll: (capabilities: readonly Capability[]) => boolean;
  /** Re-run the server authorization loader (used after a detected role change). */
  refresh: () => void;
}

const AuthorizationContext = createContext<AuthorizationValue | null>(null);

export function AuthorizationProvider({
  authorization,
  children,
}: {
  authorization: AuthorizationInfo;
  children: ReactNode;
}) {
  const router = useRouter();

  // Derived from props on every render, never copied into independent state, so
  // capabilities cannot go stale when the server sends refreshed authorization.
  const value = useMemo<AuthorizationValue>(() => {
    const view = createAuthorizationView(authorization);
    return {
      ...view,
      refresh: () => router.refresh(),
    };
  }, [authorization, router]);

  return (
    <AuthorizationContext.Provider value={value}>
      {children}
    </AuthorizationContext.Provider>
  );
}

export function useAuthorization(): AuthorizationValue {
  const value = useContext(AuthorizationContext);
  if (!value) {
    throw new Error(
      "useAuthorization must be used within an AuthorizationProvider",
    );
  }
  return value;
}
