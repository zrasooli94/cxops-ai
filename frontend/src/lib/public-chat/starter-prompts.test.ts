import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  availableChatStarterPrompts,
  CHAT_STARTER_PROMPTS,
  shouldShowChatStarters,
} from "./starter-prompts.ts";

describe("customer-facing chatbot starters", () => {
  it("offers two distinct, authored, tenant-neutral drafts", () => {
    assert.deepEqual(
      CHAT_STARTER_PROMPTS.map((starter) => starter.id),
      ["business", "support"],
    );
    for (const starter of CHAT_STARTER_PROMPTS) {
      assert.ok(starter.label.trim());
      assert.ok(starter.draft.endsWith(".") || starter.draft.endsWith("?"));
      assert.ok(starter.draft.length < 100);
      assert.doesNotMatch(starter.draft, /https?:|mailto:|<[^>]+>/i);
      assert.doesNotMatch(starter.draft, /guarantee|available now|24\/7/i);
    }
  });

  it("never proposes drafts that exceed the tenant message cap", () => {
    const max = Math.max(...CHAT_STARTER_PROMPTS.map((s) => s.draft.length));
    assert.equal(availableChatStarterPrompts(max).length, 2);
    assert.ok(availableChatStarterPrompts(max - 1).length < 2);
    assert.equal(availableChatStarterPrompts(1).length, 0);
    assert.equal(availableChatStarterPrompts(0).length, 0);
    assert.equal(availableChatStarterPrompts(-1).length, 0);
    assert.equal(availableChatStarterPrompts(NaN).length, 0);
    assert.equal(availableChatStarterPrompts(Infinity).length, 0);
    assert.equal(availableChatStarterPrompts(3.14).length, 0);
    assert.equal(availableChatStarterPrompts(4000).length, 2);
  });

  it("only shows suggestions on an untouched active AI conversation", () => {
    const idle = {
      status: "ai_active",
      hasVisitorMessage: false,
      hasDraft: false,
      busy: false,
    };
    assert.equal(shouldShowChatStarters(idle), true);
    assert.equal(
      shouldShowChatStarters({ ...idle, hasVisitorMessage: true }),
      false,
    );
    assert.equal(
      shouldShowChatStarters({ ...idle, hasDraft: true }),
      false,
    );
    assert.equal(shouldShowChatStarters({ ...idle, busy: true }), false);
    for (const status of [
      "connecting",
      "error",
      "human_requested",
      "human_assigned",
      "closed",
      "unknown",
    ]) {
      assert.equal(
        shouldShowChatStarters({ ...idle, status }),
        false,
        status,
      );
    }
  });

  it("never includes an automatic send, tool execution or a staff promise", () => {
    for (const starter of CHAT_STARTER_PROMPTS) {
      assert.doesNotMatch(
        starter.label + " " + starter.draft,
        /\b(send now|purchase|execute|your ticket is|we will reply)\b/i,
      );
    }
  });
});
