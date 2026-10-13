import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  cursorOf,
  LEGACY_POLL_INTERVAL_MS,
  mergeMessages,
  nextPollDelay,
} from "./updates.ts";
import type { PublicChatMessageRead } from "./types.ts";

function msg(
  id: number,
  direction: string,
  body: string,
): PublicChatMessageRead {
  return { id, direction, body, sent_at: null };
}

describe("nextPollDelay", () => {
  it("follows the server hint within bounds", () => {
    assert.equal(nextPollDelay(1000, false), 1000);
    assert.equal(nextPollDelay(3000, false), 3000);
    assert.equal(nextPollDelay(50, false), 1000);
    assert.equal(nextPollDelay(600_000, false), 20_000);
  });

  it("falls back to the legacy interval without a hint", () => {
    assert.equal(nextPollDelay(null, false), LEGACY_POLL_INTERVAL_MS);
    assert.equal(nextPollDelay(Number.NaN, false), LEGACY_POLL_INTERVAL_MS);
  });

  it("slows down in a hidden tab", () => {
    assert.equal(nextPollDelay(1000, true), 30_000);
  });
});

describe("cursorOf", () => {
  it("ignores local placeholders", () => {
    assert.equal(cursorOf([]), 0);
    assert.equal(
      cursorOf([msg(-1, "outbound", "hi"), msg(7, "inbound", "a"), msg(-5, "inbound", "b")]),
      7,
    );
  });
});

describe("mergeMessages", () => {
  it("replaces the optimistic copy and appends the reply once", () => {
    const previous = [msg(-1, "outbound", "Welcome"), msg(-99, "inbound", "Where is my order?")];
    const incoming = [msg(10, "inbound", "Where is my order?"), msg(11, "outbound", "Let me check.")];
    const merged = mergeMessages(previous, incoming);
    assert.deepEqual(
      merged.map((m) => m.id),
      [-1, 10, 11],
    );
    assert.deepEqual(mergeMessages(merged, incoming), merged);
  });

  it("replaces a reply shown from the send response with its stored copy", () => {
    const previous = [
      msg(10, "inbound", "human please"),
      msg(-123, "outbound", "A teammate will reply here."),
    ];
    const merged = mergeMessages(previous, [msg(11, "outbound", "A teammate will reply here.")]);
    assert.deepEqual(merged.map((m) => m.id), [10, 11]);
  });

  it("keeps an optimistic message whose stored copy has not arrived", () => {
    const previous = [msg(-99, "inbound", "second")];
    const merged = mergeMessages(previous, [msg(11, "outbound", "reply")]);
    assert.deepEqual(merged.map((m) => m.id), [-99, 11]);
  });
});
