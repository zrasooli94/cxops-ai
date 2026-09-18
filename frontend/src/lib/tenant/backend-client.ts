"use server";

import {
  AUTHORIZATION_PATH,
  loadAuthorization,
  tenantSelectorHeaderValue,
} from "@/lib/authorization/helpers";
import type { AuthorizationInfo } from "@/lib/authorization/types";
import { createNhostServerClient } from "@/lib/nhost/server";

const BACKEND_API_URL =
  process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

const TENANT_SELECTOR_HEADER = "X-CXOps-Organization-ID";

export interface OrganizationMembership {
  id: number;
  name: string;
  industry: string | null;
  external_id: string | null;
  created_at: string;
}

export interface TenantInfo {
  organization_id: number;
  organization_name: string;
}

class TenantSelectionError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function getAuthorizationHeader(): Promise<string | null> {
  const nhost = await createNhostServerClient();
  const session = nhost.getUserSession();

  if (!session?.accessToken) {
    return null;
  }

  const refreshed = await nhost.refreshSession(60);
  if (!refreshed?.accessToken) {
    nhost.clearSession();
    return null;
  }

  return `Bearer ${refreshed.accessToken}`;
}

async function backendFetch(
  path: string,
  options: RequestInit & { organizationId?: number } = {},
): Promise<Response> {
  const authHeader = await getAuthorizationHeader();
  if (!authHeader) {
    throw new Error("Authentication required");
  }

  const headers = new Headers(options.headers);
  headers.set("authorization", authHeader);
  headers.set("content-type", "application/json");
  if (options.organizationId) {
    headers.set(
      TENANT_SELECTOR_HEADER,
      tenantSelectorHeaderValue(options.organizationId),
    );
  }

  return fetch(`${BACKEND_API_URL}${path}`, {
    ...options,
    headers,
    cache: "no-store",
  });
}

/**
 * List organizations the authenticated subject is a member of.
 *
 * This is used by the organization picker before a tenant has been selected.
 */
export async function getMyOrganizations(): Promise<OrganizationMembership[]> {
  const response = await backendFetch("/me/organizations");

  if (response.status === 401) {
    throw new Error("Authentication required");
  }
  if (!response.ok) {
    throw new Error("Could not load organizations");
  }

  return response.json();
}

/**
 * Load the authenticated subject's role and capabilities for the supplied
 * organization.
 *
 * The caller passes the already-resolved organization id; this function does NOT
 * read the organization cookie. FastAPI resolves authorization from the tenant
 * selector header against the authenticated subject's membership — the result is
 * never inferred from JWT claims, role names, or client-side selection.
 */
export async function getMyAuthorization(
  organizationId: number,
): Promise<AuthorizationInfo> {
  return loadAuthorization(
    (resolvedOrganizationId) =>
      backendFetch(AUTHORIZATION_PATH, {
        organizationId: resolvedOrganizationId,
      }),
    organizationId,
  );
}

/**
 * Validate that ``organizationId`` is a current membership of the authenticated
 * subject by asking FastAPI to resolve the tenant with that selector.
 *
 * On success the caller may safely persist the id as UX state. On failure the
 * cookie must NOT be updated.
 */
export async function validateOrganizationSelection(
  organizationId: number,
): Promise<TenantInfo> {
  const response = await backendFetch("/me/tenant", {
    organizationId,
  });

  if (response.status === 401) {
    throw new TenantSelectionError("Authentication required", 401);
  }
  if (response.status === 403) {
    throw new TenantSelectionError(
      "Organization membership not found",
      403,
    );
  }
  if (response.status === 409) {
    throw new TenantSelectionError(
      "Multiple organizations configured; select one",
      409,
    );
  }
  if (!response.ok) {
    throw new TenantSelectionError("Could not validate selection", response.status);
  }

  return response.json();
}

/**
 * Resolve the active tenant for the authenticated subject using the supplied
 * organization selector. Mirrors ``validateOrganizationSelection`` but named
 * for the layout read path.
 */
export async function getMyTenant(
  organizationId: number,
): Promise<TenantInfo> {
  return validateOrganizationSelection(organizationId);
}

export { TenantSelectionError };
