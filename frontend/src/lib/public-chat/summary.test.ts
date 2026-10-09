import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  SUMMARY_WINDOWS,
  fetchPublicChatSummary,
  isSummaryWindow,
} from "./staff.ts";
import { PublicChatApiError } from "./client.ts";
import type { PublicChatPilotSummary } from "./types.ts";

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

const SUMMARY: PublicChatPilotSummary = {
  tenant_id: 42,
  window: "24h",
  generated_at: "2026-10-09T12:00:00Z",
  config: {
    widget_enabled: true,
    grounded_auto_reply_enabled: true,
    theme_token: "midnight",
    allowed_origin_count: 1,
  },
  queue: {
    human_requested: 1,
    human_assigned: 0,
    ai_active: 2,
    active_total: 3,
  },
  queue_health: {
    health: "normal",
    oldest_waiting_since: "2026-10-09T11:30:00Z",
    oldest_waiting_minutes: 12,
  },
  window_summary: {
    sessions_created: 5,
    customer_messages: 12,
    sessions_closed: 2,
    grounded_public_auto_replies: 3,
  },
  rag: {
    rag_requests: 9,
    rag_grounded: 6,
    rag_errors: 0,
    avg_rag_latency_ms: 120,
    avg_best_similarity: 0.81,
    estimated_ai_cost_usd: 0.01,
  },
  safety: {
    public_chat_integration_jobs: 0,
    autonomous_public_chat_executions: 0,
  },
};

describe("SUMMARY_WINDOWS", () => {
  it("is the closed set of backend-supported windows", () => {
    assert.deepEqual(SUMMARY_WINDOWS, ["24h", "7d"]);
  });
});

describe("isSummaryWindow", () => {
  it("accepts only the two supported windows", () => {
    assert.equal(isSummaryWindow("24h"), true);
    assert.equal(isSummaryWindow("7d"), true);
    assert.equal(isSummaryWindow("30d"), false);
    assert.equal(isSummaryWindow("720h"), false);
    assert.equal(isSummaryWindow(""), false);
  });
});

describe("fetchPublicChatSummary", () => {
  it("requests the summary through the backend proxy with the window", async () => {
    const calls: Array<{
      url: RequestInfo | URL;
      init: RequestInit | undefined;
    }> = [];
    const fetcher = recordingFetcher(calls, jsonResponse(SUMMARY));

    const result = await fetchPublicChatSummary("24h", fetcher);

    assert.deepEqual(result, SUMMARY);
    assert.equal(
      String(calls[0]?.url),
      "/api/backend/staff/public-chat/summary?window=24h",
    );
    assert.equal(calls[0]?.init?.cache, "no-store");
  });

  it("passes the 7d window through the proxy", async () => {
    const calls: Array<{
      url: RequestInfo | URL;
      init: RequestInit | undefined;
    }> = [];
    const fetcher = recordingFetcher(calls, jsonResponse({ ...SUMMARY, window: "7d" }));

    const result = await fetchPublicChatSummary("7d", fetcher);

    assert.equal(result.window, "7d");
    assert.equal(
      String(calls[0]?.url),
      "/api/backend/staff/public-chat/summary?window=7d",
    );
  });

  it("throws PublicChatApiError with the detail on 422", async () => {
    const fetcher = recordingFetcher(
      [],
      jsonResponse({ detail: "Invalid window" }, 422),
    );

    await assert.rejects(
      fetchPublicChatSummary("24h", fetcher),
      (error) => {
        assert.ok(error instanceof PublicChatApiError);
        assert.equal(error.status, 422);
        assert.equal(error.message, "Invalid window");
        return true;
      },
    );
  });

  it("throws PublicChatApiError on a denied request", async () => {
    const fetcher = recordingFetcher(
      [],
      jsonResponse({ detail: "Insufficient permissions" }, 403),
    );

    await assert.rejects(
      fetchPublicChatSummary("24h", fetcher),
      (error) => {
        assert.ok(error instanceof PublicChatApiError);
        assert.equal(error.status, 403);
        return true;
      },
    );
  });
});