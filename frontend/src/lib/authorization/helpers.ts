/**
 * Pure authorization helpers.
 *
 * Everything in this module is deterministic, dependency-free, and safe to run
 * on both the server and the client. It contains no policy: capability strings
 * are matched exactly as the backend returned them, and classification of
 * backend failures is conservative.
 *
 * Frontend authorization is UX only. FastAPI remains the sole authority.
 */
import type { AuthorizationInfo } from "./types";

/** FastAPI detail returned by `RequireCapability` when the role lacks a capability. */
export const CAPABILITY_FORBIDDEN_DETAIL = "Insufficient permissions";

/** Known backend details that mean the tenant membership/selection is invalid. */
const MEMBERSHIP_AUTHORIZATION_DETAILS: readonly string[] = [
  "Organization membership not found",
  "No active organization membership",
  "Invalid organization selection",
];

/** Prefix of the 409 detail returned when the tenant selector is required. */
const ORGANIZATION_SELECTION_CONFLICT_PREFIX =
  "Multiple organization memberships configured";

export function hasCapability(
  capabilities: readonly string[],
  capability: string,
): boolean {
  return capabilities.includes(capability);
}

export function hasAnyCapability(
  capabilities: readonly string[],
  required: readonly string[],
): boolean {
  return required.some((capability) => capabilities.includes(capability));
}

export function hasAllCapabilities(
  capabilities: readonly string[],
  required: readonly string[],
): boolean {
  // Fail closed: an empty requirement must never be treated as authorization
  // success, otherwise a misconfigured empty requirement would unlock a view.
  if (required.length === 0) {
    return false;
  }
  return required.every((capability) => capabilities.includes(capability));
}

/**
 * A capability denial means the organization is valid but the subject lacks
 * permission for the operation. It must never be treated as proof that the
 * organization selection is invalid.
 */
export function isCapabilityForbidden(detail: unknown): boolean {
  return detail === CAPABILITY_FORBIDDEN_DETAIL;
}

/**
 * A membership/tenant failure means the selected organization is no longer
 * resolvable for the subject (revoked membership, stale selector). Only the
 * known, explicit backend details qualify; unknown details do not.
 */
export function isMembershipAuthorizationError(detail: unknown): boolean {
  return (
    typeof detail === "string" &&
    MEMBERSHIP_AUTHORIZATION_DETAILS.includes(detail)
  );
}

/** True only for the 409 that asks the client to explicitly select an organization. */
export function isOrganizationSelectionConflict(detail: unknown): boolean {
  return (
    typeof detail === "string" &&
    detail.startsWith(ORGANIZATION_SELECTION_CONFLICT_PREFIX)
  );
}

export type AuthorizationFailureDisposition =
  | "capability"
  | "membership"
  | "organization-selection"
  | "unknown";

/**
 * Classify a backend authorization failure from its status and `detail`.
 *
 * Classification is deliberately conservative and never keys on HTTP status
 * alone: an unrecognized 403 or 409 is "unknown".
 */
export function classifyAuthorizationFailure(
  status: number,
  detail: unknown,
): AuthorizationFailureDisposition {
  if (status === 403) {
    if (isCapabilityForbidden(detail)) {
      return "capability";
    }
    if (isMembershipAuthorizationError(detail)) {
      return "membership";
    }
    return "unknown";
  }

  if (status === 409 && isOrganizationSelectionConflict(detail)) {
    return "organization-selection";
  }

  return "unknown";
}

/**
 * Whether the active organization selection should be cleared after a backend
 * response. Only a genuine membership/tenant failure or selector conflict
 * qualifies. A capability denial preserves the current selection, and unknown
 * responses are left untouched.
 */
export function shouldClearOrganizationSelection(
  status: number,
  detail: unknown,
): boolean {
  const disposition = classifyAuthorizationFailure(status, detail);
  return disposition === "membership" || disposition === "organization-selection";
}

/**
 * Safely extract a backend `{ "detail": string }` message from a response body.
 * Returns null for non-JSON, malformed, or non-string details.
 */
export function readErrorDetail(
  rawBody: string,
  contentType: string | null | undefined,
): string | null {
  if (!contentType || !contentType.toLowerCase().includes("json")) {
    return null;
  }

  try {
    const parsed: unknown = JSON.parse(rawBody);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail?: unknown }).detail;
      return typeof detail === "string" ? detail : null;
    }
    return null;
  } catch {
    return null;
  }
}

/** Path of the backend authorization endpoint. */
export const AUTHORIZATION_PATH = "/me/authorization";

/**
 * Serialize the resolved organization id for the tenant selector header.
 * Kept pure so the value sent to FastAPI is explicit and testable.
 */
export function tenantSelectorHeaderValue(organizationId: number): string {
  return String(organizationId);
}

/**
 * Validate an unknown backend payload into `AuthorizationInfo`.
 *
 * Returns null on any shape mismatch so callers can fail safely (no partial or
 * coerced authorization context is ever produced).
 */
export function parseAuthorizationInfo(
  value: unknown,
): AuthorizationInfo | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const candidate = value as Record<string, unknown>;
  const organizationId = candidate.organization_id;
  const role = candidate.role;
  const capabilities = candidate.capabilities;

  if (
    typeof organizationId !== "number" ||
    !Number.isInteger(organizationId) ||
    organizationId <= 0
  ) {
    return null;
  }
  if (typeof role !== "string") {
    return null;
  }
  if (
    !Array.isArray(capabilities) ||
    !capabilities.every((capability) => typeof capability === "string")
  ) {
    return null;
  }

  return {
    organization_id: organizationId,
    role,
    capabilities,
  };
}

/** Structural view a Server Component hands to the client provider. */
export interface AuthorizationView {
  organizationId: number;
  role: string;
  capabilities: readonly string[];
  can(capability: string): boolean;
  canAny(required: readonly string[]): boolean;
  canAll(required: readonly string[]): boolean;
}

/**
 * Derive a capability view from backend-provided authorization.
 *
 * All decisions use the capability set only. `role` is carried for display and
 * never consulted for a permission decision.
 */
export function createAuthorizationView(
  authorization: AuthorizationInfo,
): AuthorizationView {
  const capabilities = authorization.capabilities;

  return {
    organizationId: authorization.organization_id,
    role: authorization.role,
    capabilities,
    can: (capability) => hasCapability(capabilities, capability),
    canAny: (required) => hasAnyCapability(capabilities, required),
    canAll: (required) => hasAllCapabilities(capabilities, required),
  };
}

/** Minimal fetch response surface required to load authorization. */
export interface AuthorizationResponse {
  status: number;
  ok: boolean;
  json(): Promise<unknown>;
}

/**
 * Why an authorization load failed.
 *
 * - `authentication` — no valid session; existing auth handling applies.
 * - `membership` — the selected organization is no longer resolvable.
 * - `unavailable` — the backend could not answer (transport/5xx).
 * - `malformed` — a 200 without a valid AuthorizationInfo shape.
 * - `mismatch` — a valid shape for a different organization than requested.
 */
export type AuthorizationLoadFailure =
  | "authentication"
  | "membership"
  | "unavailable"
  | "malformed"
  | "mismatch";

/**
 * Typed failure raised by `loadAuthorization` so callers can route to the
 * correct recovery path without parsing messages. The `reason` is never derived
 * from untrusted input; it is decided here from the HTTP status and shape.
 */
export class AuthorizationLoadError extends Error {
  readonly reason: AuthorizationLoadFailure;

  constructor(message: string, reason: AuthorizationLoadFailure) {
    super(message);
    this.name = "AuthorizationLoadError";
    this.reason = reason;
  }
}

/**
 * Load and validate authorization through an injected fetcher.
 *
 * Kept dependency-injected so the request/validation flow can be tested with the
 * Node built-in runner without importing the server-only backend client.
 *
 * `GET /me/authorization` requires identity + tenant membership and no
 * capability, so a 403/409 here is always a membership/tenant failure.
 */
export async function loadAuthorization(
  fetchAuthorization: (organizationId: number) => Promise<AuthorizationResponse>,
  organizationId: number,
): Promise<AuthorizationInfo> {
  const response = await fetchAuthorization(organizationId);

  if (response.status === 401) {
    throw new AuthorizationLoadError("Authentication required", "authentication");
  }
  if (response.status === 403 || response.status === 409) {
    throw new AuthorizationLoadError(
      "Could not load authorization",
      "membership",
    );
  }
  if (!response.ok) {
    throw new AuthorizationLoadError(
      "Could not load authorization",
      "unavailable",
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new AuthorizationLoadError(
      "Invalid authorization response",
      "malformed",
    );
  }

  const authorization = parseAuthorizationInfo(payload);
  if (!authorization) {
    throw new AuthorizationLoadError(
      "Invalid authorization response",
      "malformed",
    );
  }

  // Defense-in-depth: never mount authorization derived for a different
  // organization than the one the tenant flow resolved. FastAPI always echoes
  // the resolved tenant, so a mismatch means something is wrong; fail closed.
  if (authorization.organization_id !== organizationId) {
    throw new AuthorizationLoadError(
      "Invalid authorization response",
      "mismatch",
    );
  }

  return authorization;
}
