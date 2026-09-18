"use server";

import { cookies } from "next/headers";
import {
  AUTHORIZATION_PATH,
  loadAuthorization,
  AuthorizationLoadError,
  tenantSelectorHeaderValue,
} from "@/lib/authorization/helpers";
import type { AuthorizationInfo } from "@/lib/authorization/types";
import { createNhostServerClient } from "@/lib/nhost/server";
import {
  SESSION_COOKIE_NAME,
  deserializeSession,
} from "@/lib/session/helpers";
import { accessTokenAuthHeader } from "@/lib/session/read";

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
    throw new AuthorizationLoadError("Authentication required", "authentication");
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
 * READ-ONLY authorization header for Server Component rendering.
 *
 * Built from the session cookie without constructing the Nhost client and
 * without refreshing: refresh (and the cookie write it performs) is only legal
 * in a Server Action or Route Handler, never during RSC render. Returns null
 * when the cookie carries no usable access token.
 */
async function getReadOnlyAuthHeader(): Promise<string | null> {
  const cookieStore = await cookies();
  const raw = cookieStore.get(SESSION_COOKIE_NAME)?.value ?? null;
  return accessTokenAuthHeader(deserializeSession(raw));
}

async function backendFetchReadOnly(
  path: string,
  options: RequestInit & { organizationId?: number } = {},
): Promise<Response> {
  const authHeader = await getReadOnlyAuthHeader();
  if (!authHeader) {
    throw new AuthorizationLoadError("Authentication required", "authentication");
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

function membershipFromJson(payload: unknown): OrganizationMembership[] {
  if (!Array.isArray(payload)) {
    throw new AuthorizationLoadError(
      "Could not load organizations",
      "unavailable",
    );
  }
  return payload as OrganizationMembership[];
}

/**
 * List organizations the authenticated subject is a member of.
 *
 * This is used by the organization picker before a tenant has been selected
 * and by the landing Route Handler. Failures are raised as typed
 * `AuthorizationLoadError`s so the landing handler can route an authentication
 * failure through the session-recovery Route Handler instead of 500-ing.
 */
export async function getMyOrganizations(): Promise<OrganizationMembership[]> {
  let response: Response;
  try {
    response = await backendFetch("/me/organizations");
  } catch (error) {
    if (error instanceof AuthorizationLoadError) throw error;
    throw new AuthorizationLoadError(
      "Could not load organizations",
      "unavailable",
    );
  }

  if (response.status === 401) {
    throw new AuthorizationLoadError("Authentication required", "authentication");
  }
  if (!response.ok) {
    throw new AuthorizationLoadError(
      "Could not load organizations",
      "unavailable",
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new AuthorizationLoadError(
      "Could not load organizations",
      "unavailable",
    );
  }

  return membershipFromJson(payload);
}

/**
 * READ-ONLY membership list for Server Component rendering.
 *
 * Uses the access token already present in the session cookie - never refreshes
 * and never writes cookies. On failure it raises a typed
 * `AuthorizationLoadError` so the caller can route authentication failures to
 * the recovery Route Handler without a 500.
 */
export async function getMyOrganizationsReadOnly(): Promise<OrganizationMembership[]> {
  let response: Response;
  try {
    response = await backendFetchReadOnly("/me/organizations");
  } catch (error) {
    if (error instanceof AuthorizationLoadError) throw error;
    throw new AuthorizationLoadError(
      "Could not load organizations",
      "unavailable",
    );
  }

  if (response.status === 401) {
    throw new AuthorizationLoadError("Authentication required", "authentication");
  }
  if (!response.ok) {
    throw new AuthorizationLoadError(
      "Could not load organizations",
      "unavailable",
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new AuthorizationLoadError(
      "Could not load organizations",
      "unavailable",
    );
  }

  return membershipFromJson(payload);
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
 * READ-ONLY authorization load for Server Component rendering.
 *
 * Mirror of `getMyAuthorization` that uses the cookie access token as-is and
 * never refreshes, so no cookie write can happen during render. Raises the
 * same typed `AuthorizationLoadError`s as `loadAuthorization`.
 */
export async function getMyAuthorizationReadOnly(
  organizationId: number,
): Promise<AuthorizationInfo> {
  return loadAuthorization(
    (resolvedOrganizationId) =>
      backendFetchReadOnly(AUTHORIZATION_PATH, {
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
