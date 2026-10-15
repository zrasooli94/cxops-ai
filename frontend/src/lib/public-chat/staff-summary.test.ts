import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { PublicChatApiError } from "./client.ts";
import { fetchPublicChatSummary, isSummaryWindow } from "./staff.ts";

describe("dashboard handoff summary API contract", () => {
  it("requests only the authenticated, uncached 24-hour summary", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const response = { queue: { human_requested: 2, human_assigned: 1 },
      queue_health: { health: "normal", oldest_waiting_minutes: 5 } };
    const fetcher = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      requests.push({ url: String(input), init });
      return new Response(JSON.stringify(response), { status: 200 });
    };
    const result = await fetchPublicChatSummary("24h", fetcher);
    assert.equal(requests.length, 1);
    assert.equal(requests[0]?.url, "/api/backend/staff/public-chat/summary?window=24h");
    assert.equal(requests[0]?.init?.cache, "no-store");
    assert.deepEqual(result.queue, response.queue);
  });

  it("refuses to treat a forbidden tenant summary as an empty queue", async () => {
    const fetcher = async (): Promise<Response> =>
      new Response(JSON.stringify({ detail: "Insufficient permissions" }), { status: 403 });
    await assert.rejects(fetchPublicChatSummary("24h", fetcher), (err: unknown) => {
      assert.ok(err instanceof PublicChatApiError);
      assert.equal(err.status, 403);
      return true;
    });
  });

  it("does not silently treat upstream failures as zero handoffs", async () => {
    const fetcher = async (): Promise<Response> => new Response("Unavailable", { status: 503 });
    await assert.rejects(fetchPublicChatSummary("7d", fetcher), (err: unknown) => {
      assert.ok(err instanceof PublicChatApiError);
      assert.equal(err.status, 503);
      return true;
    });
  });

  it("constrains selectable windows to backend-supported values", () => {
    assert.equal(isSummaryWindow("24h"), true);
    assert.equal(isSummaryWindow("7d"), true);
    assert.equal(isSummaryWindow("30d"), false);
  });
});
