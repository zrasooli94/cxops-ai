import {
  NextRequest,
  NextResponse,
} from "next/server";
import { cookies } from "next/headers";

import {
  buildBackendHeaders,
} from "@/lib/auth/proxy-headers";
import {
  readErrorDetail,
  shouldClearOrganizationSelection,
} from "@/lib/authorization/helpers";
import {
  clearActiveOrganizationId,
  getActiveOrganizationId,
} from "@/lib/tenant/cookie";
import { ACTIVE_ORGANIZATION_COOKIE_NAME } from "@/lib/tenant/cookie-helpers";
import { refreshSessionOnce, type BffRefreshDeps } from "@/lib/session/bff-helpers";
import { SESSION_COOKIE_NAME } from "@/lib/session/helpers";
import { createNhostServerClient } from "@/lib/nhost/server";

const BACKEND_API_URL =
  process.env.BACKEND_API_URL ??
  "http://127.0.0.1:8000";

const isDevelopment = process.env.NODE_ENV !== "production";

async function createRefreshDeps(): Promise<BffRefreshDeps> {
  const nhost = await createNhostServerClient();
  const cookieStore = await cookies();

  return {
    getSession: () => nhost.getUserSession(),
    refresh: (marginSeconds) => nhost.refreshSession(marginSeconds),
    clearSession: () => nhost.clearSession(),
    deleteSessionCookie: () => cookieStore.delete(SESSION_COOKIE_NAME),
    deleteOrganizationCookie: () =>
      cookieStore.delete(ACTIVE_ORGANIZATION_COOKIE_NAME),
  };
}

async function getAuthHeader(): Promise<string | null> {
  const { authHeader } = await refreshSessionOnce(
    await createRefreshDeps(),
    60,
  );

  return authHeader;
}

async function proxy(
  request: NextRequest,
  context: {
    params: Promise<{
      path: string[];
    }>;
  },
) {
  const { path } = await context.params;

  const backendUrl = new URL(
    `${BACKEND_API_URL}/${path.join("/")}`,
  );

  request.nextUrl.searchParams.forEach(
    (value, key) => {
      backendUrl.searchParams.append(key, value);
    },
  );

  const authHeader = await getAuthHeader();
  const activeOrganizationId = await getActiveOrganizationId();

  const { headers, rejectUnauthorized } = buildBackendHeaders(request.headers, {
    activeOrganizationId,
    authHeader,
    isDevelopment,
  });

  if (rejectUnauthorized) {
    return NextResponse.json(
      { detail: "Authentication required." },
      { status: 401 },
    );
  }

  // Buffer request body for potential retry
  let requestBody: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    requestBody = await request.arrayBuffer();
  }

  const init: RequestInit = {
    method: request.method,
    headers,
    cache: "no-store",
    body: requestBody,
  };

  try {
    const response = await fetch(backendUrl, init);

    if (response.status === 401) {
      // Exactly ONE forced refresh retry; a failed forced refresh also clears
      // the session and active-organization cookies.
      const { authHeader: retryAuthHeader } = await refreshSessionOnce(
        await createRefreshDeps(),
        0,
      );

      if (retryAuthHeader) {
        const retryHeaders = new Headers(init.headers);
        retryHeaders.set("authorization", retryAuthHeader);

        const retryResponse = await fetch(backendUrl, {
          ...init,
          headers: retryHeaders,
        });

        if (retryResponse.status !== 401) {
          const retryBody = await retryResponse.arrayBuffer();
          return new NextResponse(retryBody, {
            status: retryResponse.status,
            headers: {
              "content-type":
                retryResponse.headers.get("content-type") ?? "application/json",
            },
          });
        }
      }

      // If we get here, the retry also failed or session invalid
      return new NextResponse(
        JSON.stringify({ detail: "Authentication expired." }),
        { status: 401 },
      );
    }

    const contentType =
      response.headers.get("content-type") ?? "application/json";
    const body = await response.arrayBuffer();

    // Only clear the active organization selection when the backend reports a
    // genuine membership/tenant failure or an organization-selection conflict.
    // A capability denial (403 "Insufficient permissions") means the
    // organization is valid but the subject lacks permission, so the selection
    // must be preserved. Unknown 403/409 responses are left untouched; a
    // permission failure is never proof that the membership is invalid.
    if (response.status === 403 || response.status === 409) {
      const detail = readErrorDetail(new TextDecoder().decode(body), contentType);
      if (shouldClearOrganizationSelection(response.status, detail)) {
        await clearActiveOrganizationId();
      }
    }

    return new NextResponse(body, {
      status: response.status,
      headers: {
        "content-type": contentType,
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "CXOps backend is unavailable." },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;