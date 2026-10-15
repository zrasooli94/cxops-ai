import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { toHandoffOverview } from "./handoff-overview.ts";
import type { PublicChatPilotSummary } from "../public-chat/types.ts";

const fixture = {
  queue: { human_requested: 3, human_assigned: 2, ai_active: 7, active_total: 12 },
  queue_health: { health: "attention", oldest_waiting_since: "2026-10-15T00:00:00Z", oldest_waiting_minutes: 21 },
} as PublicChatPilotSummary;

describe("staff handoff overview", () => {
  it("maps only bounded queue aggregates, never customer/session details", () => {
    assert.deepEqual(toHandoffOverview(fixture), {
      waiting: 3, assigned: 2, oldestWaitingMinutes: 21, needsAttention: true,
    });
  });
  it("handles an empty waiting queue with no age", () => {
    assert.deepEqual(toHandoffOverview({
      ...fixture, queue: { ...fixture.queue, human_requested: 0 },
      queue_health: { health: "normal", oldest_waiting_since: null, oldest_waiting_minutes: null },
    }), {
      waiting: 0, assigned: 2, oldestWaitingMinutes: null, needsAttention: false,
    });
  });
});
