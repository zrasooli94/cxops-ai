import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  isNearChatBottom,
  messagePresentation,
  NEAR_BOTTOM_PX,
} from "./conversation-view.ts";

describe("visitor-facing chat bubbles", () => {
  it("places visitors on the right and support on the left", () => {
    assert.deepEqual(messagePresentation("inbound"), {
      alignSelf: "flex-end",
      sender: "You",
    });
    assert.deepEqual(messagePresentation("outbound"), {
      alignSelf: "flex-start",
      sender: "Support",
    });
  });

  it("does not grant customer identity to an unexpected direction", () => {
    for (const direction of ["internal", "", "INBOUND", "unknown"]) {
      assert.deepEqual(messagePresentation(direction), {
        alignSelf: "flex-start",
        sender: "Support",
      });
    }
  });
});

describe("respect the visitor's conversation scroll", () => {
  it("follows at the bottom, at the exact boundary and nearby", () => {
    assert.equal(isNearChatBottom(1000, 700, 300), true);
    assert.equal(isNearChatBottom(1000, 620, 300), true);
    assert.equal(isNearChatBottom(1000, 650, 300), true);
    assert.equal(NEAR_BOTTOM_PX, 80);
  });

  it("does not jump someone who scrolled back to read older messages", () => {
    assert.equal(isNearChatBottom(1000, 619, 300), false);
    assert.equal(isNearChatBottom(1000, 40, 300), false);
  });

  it("tolerates a short conversation and fractional pixel geometry", () => {
    assert.equal(isNearChatBottom(180, 0, 400), true);
    assert.equal(isNearChatBottom(1000.5, 620.5, 300), true);
  });

  it("never force-scrolls when the geometry is invalid", () => {
    for (const value of [NaN, Infinity, -Infinity, -1]) {
      assert.equal(isNearChatBottom(value, 0, 300), false);
      assert.equal(isNearChatBottom(1000, value, 300), false);
      assert.equal(isNearChatBottom(1000, 700, value), false);
    }
  });
});
