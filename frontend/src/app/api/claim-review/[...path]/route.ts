/** C12 explicitly development-only, same-origin, strictly allowlisted BFF.
 *
 * The generic /api/backend route refreshes an Nhost session; C12 instead
 * reads the existing valid JWT cookie without contacting identity providers.
 * It rechecks the backend's current org capabilities before forwarding, and
 * FastAPI C11 independently validates JWT/membership/assignment on every
 * operation. No client-selected tenant, Authorization header or backend URL.
 *
 * No real customer source text, customer data or paid API ever passes here.
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

import { mayUseClaimReview } from "@/lib/claim-review/access";
import { c12SyntheticStaffPreviewEnabled } from "@/lib/claim-review/preview";
import {
  c12ReviewerAdminPath,
  c12ReviewerBackendPath,
} from "@/lib/claim-review/server-allowlist";
import { SESSION_COOKIE_NAME, deserializeSession } from "@/lib/session/helpers";
import {
  accessTokenAuthHeader,
  classifySessionState,
} from "@/lib/session/read";
import { getMyAuthorizationReadOnly } from "@/lib/tenant/backend-client";
import { getActiveOrganizationId } from "@/lib/tenant/cookie";

const BACKEND_API_URL =
  process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

type RouteContext = { params: Promise<{ path: string[] }> };

function refuse(status: number, code = "unspecified"): NextResponse {
  // Only static category labels in a fictional CI run; no JWT/PII/body.
  if (process.env.CI === "true" && process.env.GITHUB_ACTIONS === "true") {
    console.warn("C12_PROXY_REFUSAL_" + String(status) + "_" + code);
  }
  return NextResponse.json(
    { detail: status === 404 ? "Not found" : "Synthetic reviewer request denied." },
    { status, headers: {
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
    } },
  );
}

async function handler(request: NextRequest, context: RouteContext) {
  // Hard gate BEFORE cookie reads, organization resolution, backend calls.
  // Production and staging can never enable this source-only preview.
  if (!c12SyntheticStaffPreviewEnabled()) return refuse(404, "flag");

  const { path } = await context.params;
  const backendPath = c12ReviewerBackendPath(path, request.method);
  if (!backendPath || request.nextUrl.search) return refuse(404, "route");

  const sessionCookie = (await cookies()).get(SESSION_COOKIE_NAME)?.value ?? null;
  const session = deserializeSession(sessionCookie);
  if (classifySessionState(session) !== "valid") return refuse(401, "session");
  const authorization = accessTokenAuthHeader(session);
  if (!authorization) return refuse(401, "auth_header");

  const selectedOrganizationId = await getActiveOrganizationId();
  if (selectedOrganizationId === null) return refuse(403, "tenant_missing");

  // This backend GET validates the actual JWT and membership under the
  // selected, trusted HttpOnly organization cookie (not a request header).
  let authz;
  try {
    authz = await getMyAuthorizationReadOnly(selectedOrganizationId);
  } catch {
    return refuse(403, "auth_lookup");
  }
  if (!mayUseClaimReview(authz, selectedOrganizationId)) return refuse(403, "eval_capability");
  if (
    c12ReviewerAdminPath(path) &&
    !authz.capabilities.includes("member.manage")
  ) return refuse(403, "member_manage");

  let body: string | undefined;
  if (request.method === "POST") {
    const origin = request.headers.get("origin");
    // Next may normalize 127.0.0.1 to localhost inside nextUrl.origin.
    // Compare with the actual browser-visible Host, preserving the exact
    // scheme and refusing foreign origins rather than skipping this check.
    const host = request.headers.get("host");
    if (!host) return refuse(403, "host");
    const expectedOrigin = request.nextUrl.protocol + "//" + host;
    if (origin && origin !== expectedOrigin) return refuse(403, "origin");
    const text = await request.text();
    if (text.length > 4096) return refuse(413);
    if (path.length === 1) {
      if (text.length !== 0) return refuse(422);
    } else {
      const type = request.headers.get("content-type") ?? "";
      if (!type.toLowerCase().startsWith("application/json")) return refuse(415);
      if (!text.trim()) return refuse(422);
      body = text;
    }
  }

  try {
    const response = await fetch(BACKEND_API_URL + backendPath, {
      method: request.method,
      headers: {
        authorization,
        "X-CXOps-Organization-ID": String(selectedOrganizationId),
        accept: "application/json",
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      ...(body === undefined ? {} : { body }),
      cache: "no-store",
    });
    const allowedStatus = [200, 201, 400, 401, 403, 404, 409, 422];
    if (!allowedStatus.includes(response.status)) return refuse(503);
    if (!response.ok) return refuse(response.status);

    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.startsWith("application/json")) return refuse(503);
    const output = await response.text();
    if (output.length > 64_000) return refuse(503);
    return new NextResponse(output, {
      status: response.status,
      headers: {
        "content-type": "application/json",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
      },
    });
  } catch {
    return refuse(503);
  }
}

export const GET = handler;
export const POST = handler;
