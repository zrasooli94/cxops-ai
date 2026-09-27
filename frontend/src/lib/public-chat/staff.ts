import type {
  StaffBusinessActionsResponse,
  StaffHandoffSession,
  StaffHandoffSessionsResponse,
} from "./types.ts";

import {
  PublicChatApiError,
} from "./client.ts";

// Staff-facing public-chat client (Phase 1P.2).
//
// Reached exclusively through the authenticated control-center proxy at
// /api/backend/... which forwards the nhost session and active-organization
// selection. These endpoints are capability-gated on the backend
// (TICKET_READ for listing, TICKET_WRITE for assign/release), so no
// authorization header is sent here.

async function parseStaffJson<T>(
  response: Response,
): Promise<T> {
  if (!response.ok) {
    let detail = "An unknown error occurred.";
    try {
      const body: unknown = await response.json();
      if (
        body !== null &&
        typeof body === "object" &&
        "detail" in body &&
        typeof (body as { detail: unknown }).detail ===
          "string"
      ) {
        detail = (body as { detail: string }).detail;
      }
    } catch {
      // Non-JSON error bodies fall back to the default message.
    }
    throw new PublicChatApiError(
      response.status,
      detail,
    );
  }
  return (await response.json()) as T;
}

export async function fetchHandoffSessions(
  fetcher: typeof fetch = fetch,
): Promise<StaffHandoffSessionsResponse> {
  const response = await fetcher(
    "/api/backend/staff/public-chat/handoff",
    { cache: "no-store" },
  );
  return parseStaffJson<StaffHandoffSessionsResponse>(response);
}

export async function assignHandoffSession(
  sessionId: number,
  fetcher: typeof fetch = fetch,
): Promise<StaffHandoffSession> {
  const response = await fetcher(
    `/api/backend/staff/public-chat/sessions/${sessionId}/assign`,
    { method: "POST", cache: "no-store" },
  );
  return parseStaffJson<StaffHandoffSession>(response);
}

export async function releaseHandoffSession(
  sessionId: number,
  fetcher: typeof fetch = fetch,
): Promise<StaffHandoffSession> {
  const response = await fetcher(
    `/api/backend/staff/public-chat/sessions/${sessionId}/release`,
    { method: "POST", cache: "no-store" },
  );
  return parseStaffJson<StaffHandoffSession>(response);
}

export async function fetchHandoffBusinessActions(
  sessionId: number,
  fetcher: typeof fetch = fetch,
): Promise<StaffBusinessActionsResponse> {
  const response = await fetcher(
    `/api/backend/staff/public-chat/sessions/${sessionId}/business-actions`,
    { cache: "no-store" },
  );
  return parseStaffJson<StaffBusinessActionsResponse>(response);
}