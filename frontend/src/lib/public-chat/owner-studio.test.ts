import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { summarizeOwnerStudio } from "./owner-studio.ts";

describe("summarizeOwnerStudio", () => {
  it("derives tenant inbox metrics without counting closed handoffs as active", () => {
    assert.deepEqual(summarizeOwnerStudio(
      [
        { status: "open", needs_response: true },
        { status: "solved", needs_response: false },
        { status: "closed", needs_response: false },
      ],
      [{ status: "human_requested" }, { status: "human_assigned" }, { status: "closed" }],
    ), { conversations: 3, awaitingReply: 1, activeHandoffs: 2, resolved: 2 });
  });
});
