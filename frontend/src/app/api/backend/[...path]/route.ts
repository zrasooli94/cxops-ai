import {
  NextRequest,
  NextResponse,
} from "next/server";

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
import { createNhostServerClient } from "@/lib/nhost/server";

const BACKEND_API_URL =
  process.env.BACKEND_API_URL ??
  "http://127.0.0.1:8000";

const isDevelopment = process.env.NODE_ENV !== "production";

async function getAuthHeader(): Promise<string | null> {
  try {
    const nhost = await createNhostServerClient();
    const session = nhost.getUserSession();

    if (!session?.accessToken) {
      return null;
    }

    // Proactively refresh if near expiry; SDK checks actual JWT exp internally
    const refreshed = await nhost.refreshSession(60);

    if (!refreshed?.accessToken) {
      // Refresh failed - clear local session
      nhost.clearSession();
      return null;
    }

    return `Bearer ${refreshed.accessToken}`;
  } catch {
    // Any error during refresh - clear local session
    try {
      const nhost = await createNhostServerClient();
      nhost.clearSession();
    } catch {
      // Ignore cleanup errors
    }
    return null;
  }
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
      // Exactly ONE forced refresh retry
      try {
        const nhost = await createNhostServerClient();
        const refreshed = await nhost.refreshSession(0);

        if (!refreshed?.accessToken) {
          // Forced refresh failed - clear local session
          nhost.clearSession();
          return new NextResponse(
            JSON.stringify({ detail: "Authentication expired." }),
            { status: 401 },
          );
        }

        const retryHeaders = new Headers(init.headers);
        retryHeaders.set("authorization", `Bearer ${refreshed.accessToken}`);

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
      } catch {
        // Forced refresh failed - clear local session
        try {
          const nhost = await createNhostServerClient();
          nhost.clearSession();
        } catch {
          // Ignore cleanup errors
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