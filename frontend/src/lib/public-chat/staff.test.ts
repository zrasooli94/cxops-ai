import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  assignHandoffSession,
  fetchHandoffBusinessActions,
  fetchHandoffSessions,
  releaseHandoffSession,
  resolveHandoffSession,
} from "./staff.ts";
import { PublicChatApiError } from "./client.ts";
import type {
  StaffHandoffSession,
} from "./types.ts";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function recordingFetcher(
  calls: Array<{
    url: RequestInfo | URL;
    init: RequestInit | undefined;
  }>,
  response: Response,
) {
  return async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    calls.push({ url: input, init });
    return response;
  };
}

const SESSION: StaffHandoffSession = {
  session_id: 12,
  conversation_id: 34,
  ticket_id: 56,
  customer_id: 7,
  status: "human_requested",
  assigned_to_subject: null,
  assigned_at: null,
  released_at: null,
  expires_at: "2026-09-27T12:00:00Z",
  created_at: "2026-09-27T13:00:00Z",
  updated_at: "2026-09-27T13:00:00Z",
};

describe("fetchHandoffSessions", () => {
  it("requests the handoff queue through the backend proxy", async () => {
    const calls: Array<{
      url: RequestInfo | URL;
      init: RequestInit | undefined;
    }> = [];
    const fetcher = recordingFetcher(
      calls,
      jsonResponse({ sessions: [SESSION] }),
    );

    const result = await fetchHandoffSessions(fetcher);

    assert.deepEqual(result, { sessions: [SESSION] });
    assert.equal(
      String(calls[0]?.url),
      "/api/backend/staff/public-chat/handoff",
    );
    assert.equal(calls[0]?.init?.cache, "no-store");
  });

  it("throws PublicChatApiError with the detail on 403", async () => {
    const fetcher = recordingFetcher(
      [],
      jsonResponse(
        { detail: "Insufficient permissions" },
        403,
      ),
    );

    await assert.rejects(
      fetchHandoffSessions(fetcher),
      (error) => {
        assert.ok(error instanceof PublicChatApiError);
        assert.equal(error.status, 403);
        assert.equal(error.message, "Insufficient permissions");
        return true;
      },
    );
  });
});

describe("assignHandoffSession", () => {
  it("posts to the assign endpoint", async () => {
    const calls: Array<{
      url: RequestInfo | URL;
      init: RequestInit | undefined;
    }> = [];
    const fetcher = recordingFetcher(
      calls,
      jsonResponse({ ...SESSION, status: "human_assigned", assigned_to_subject: "me" }),
    );

    const result = await assignHandoffSession(12, fetcher);

    assert.equal(
      String(calls[0]?.url),
      "/api/backend/staff/public-chat/sessions/12/assign",
    );
    assert.equal(calls[0]?.init?.method, "POST");
    assert.equal(result.status, "human_assigned");
    assert.equal(result.assigned_to_subject, "me");
  });
});

describe("releaseHandoffSession", () => {
  it("posts to the release endpoint", async () => {
    const calls: Array<{
      url: RequestInfo | URL;
      init: RequestInit | undefined;
    }> = [];
    const fetcher = recordingFetcher(
      calls,
      jsonResponse({ ...SESSION, status: "human_requested", assigned_to_subject: null }),
    );

    const result = await releaseHandoffSession(12, fetcher);

    assert.equal(
      String(calls[0]?.url),
      "/api/backend/staff/public-chat/sessions/12/release",
    );
    assert.equal(calls[0]?.init?.method, "POST");
    assert.equal(result.status, "human_requested");
    assert.equal(result.assigned_to_subject, null);
  });
});

describe("resolveHandoffSession", () => {
  it("posts to the resolve endpoint and returns the closed session", async () => {
    const calls: Array<{
      url: RequestInfo | URL;
      init: RequestInit | undefined;
    }> = [];
    const fetcher = recordingFetcher(
      calls,
      jsonResponse({ ...SESSION, status: "closed" }),
    );

    const result = await resolveHandoffSession(12, fetcher);

    assert.equal(
      String(calls[0]?.url),
      "/api/backend/staff/public-chat/sessions/12/resolve",
    );
    assert.equal(calls[0]?.init?.method, "POST");
    assert.equal(result.status, "closed");
  });

  it("throws PublicChatApiError with the detail on 404", async () => {
    const fetcher = recordingFetcher(
      [],
      jsonResponse({ detail: "Session not found." }, 404),
    );

    await assert.rejects(
      resolveHandoffSession(12, fetcher),
      (error) => {
        assert.ok(error instanceof PublicChatApiError);
        assert.equal(error.status, 404);
        return true;
      },
    );
  });
});

describe("fetchHandoffBusinessActions", () => {
  it("requests business actions for a session", async () => {
    const calls: Array<{
      url: RequestInfo | URL;
      init: RequestInit | undefined;
    }> = [];
    const fetcher = recordingFetcher(
      calls,
      jsonResponse({
        actions: [
          {
            id: 1,
            request_type: "a1.create_pickup_request",
            status: "approved",
            reference_id: "A1-abc123",
            summary: "Pickup scheduled",
            created_at: "2026-09-27T13:00:00Z",
            updated_at: "2026-09-27T13:05:00Z",
          },
        ],
      }),
    );

    const result = await fetchHandoffBusinessActions(12, fetcher);

    assert.equal(result.actions[0]?.reference_id, "A1-abc123");
    assert.equal(
      String(calls[0]?.url),
      "/api/backend/staff/public-chat/sessions/12/business-actions",
    );
    assert.equal(calls[0]?.init?.cache, "no-store");
  });
});