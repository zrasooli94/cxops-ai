import assert from "node:assert/strict";
import { test } from "node:test";

import {
  allHandoffText,
  handoffCopy,
  isHandoffState,
} from "./handoff-copy.ts";

test("handoff states are recognised and nothing else is", () => {
  assert.equal(isHandoffState("human_requested"), true);
  assert.equal(isHandoffState("human_assigned"), true);
  assert.equal(isHandoffState("ai_active"), false);
  assert.equal(isHandoffState("closed"), false);
});

test("a requested handoff says the assistant will not answer and offers a fresh start", () => {
  const copy = handoffCopy("human_requested");
  assert.match(copy.explanation, /will not answer further messages/);
  assert.match(copy.explanation, /start a new conversation/i);
  assert.equal(copy.newConversationLabel, "Start a new conversation");
});

test("no copy makes a response-time, availability or outcome promise", () => {
  const forbidden = [
    /within\s+\d+/i,
    /\b\d+\s*(minutes?|hours?|days?)\b/i,
    /\bshortly\b/i,
    /\bsoon\b/i,
    /\basap\b/i,
    /24\s*\/\s*7/,
    /\bimmediately\b/i,
    /\bguarantee(d)?\b(?!.*can't)/i,
    /\bwe will (reply|respond|contact)\b/i,
    /\bonline now\b/i,
  ];
  for (const text of allHandoffText()) {
    for (const pattern of forbidden) {
      assert.doesNotMatch(text, pattern, `unsupported promise in: ${text}`);
    }
  }
});

test("explicitly disclaims a reply time instead of implying one", () => {
  assert.match(
    handoffCopy("human_requested").explanation,
    /can't promise when someone will reply/,
  );
});

test("starting a new conversation is honest that it closes the old one", () => {
  const copy = handoffCopy("human_requested");
  assert.match(copy.closeWarning, /closes this one/);
  assert.match(copy.closeWarning, /no longer see it as waiting for review/);
});
