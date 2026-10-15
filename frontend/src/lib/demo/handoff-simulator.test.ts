import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  addDemoReply,
  filterDemoCases,
  freshDemoCases,
  summarizeDemo,
  transitionDemo,
} from "./handoff-simulator.ts";

describe("offline human handoff product demo", () => {
  it("starts with synthetic, clearly bounded triage metrics", () => {
    const cases = freshDemoCases();
    assert.deepEqual(summarizeDemo(cases), {
      waiting: 3,
      assigned: 2,
      resolved: 1,
      oldestWaitingMinutes: 21,
      urgentWaiting: 1,
    });
    assert.ok(cases.every((item) => item.visitor.startsWith("Demo customer")));
    assert.ok(cases.every((item) => item.id.startsWith("DEMO-")));
  });

  it("isolates reset state from previous local operations", () => {
    const original = freshDemoCases();
    const assigned = transitionDemo(original, "DEMO-1042", "assign");
    const replied = addDemoReply(assigned, "DEMO-1042", "  A sample reply  ");
    assert.equal(original[0]?.status, "waiting");
    assert.equal(original[0]?.messages.length, 3);
    assert.equal(replied[0]?.messages.at(-1)?.body, "A sample reply");
    assert.deepEqual(freshDemoCases(), original);
  });

  it("enforces assign → release / resolve transitions on the selected case only", () => {
    const initial = freshDemoCases();
    const invalidResolve = transitionDemo(initial, "DEMO-1042", "resolve");
    assert.equal(invalidResolve[0]?.status, "waiting");
    const assigned = transitionDemo(initial, "DEMO-1042", "assign");
    assert.equal(assigned[0]?.status, "assigned");
    assert.equal(assigned[0]?.owner, "You (demo)");
    assert.equal(assigned[1], initial[1]);
    const resolved = transitionDemo(assigned, "DEMO-1042", "resolve");
    assert.equal(resolved[0]?.status, "resolved");
    assert.equal(summarizeDemo(resolved).waiting, 2);
    assert.equal(summarizeDemo(resolved).resolved, 2);
    assert.equal(transitionDemo(resolved, "DEMO-1042", "release")[0]?.status, "resolved");
    const released = transitionDemo(assigned, "DEMO-1042", "release");
    assert.equal(released[0]?.status, "waiting");
    assert.equal(released[0]?.owner, null);
  });

  it("allows mock replies only to assigned conversations and never persists them", () => {
    const initial = freshDemoCases();
    const waitingReply = addDemoReply(initial, "DEMO-1042", "Hello");
    assert.deepEqual(waitingReply, initial);
    const assigned = transitionDemo(initial, "DEMO-1042", "assign");
    const after = addDemoReply(assigned, "DEMO-1042", "  Sample reply  ");
    assert.equal(after[0]?.messages.at(-1)?.body, "Sample reply");
    assert.equal(after[0]?.messages.at(-1)?.from, "staff");
    assert.equal(after[1]?.messages, initial[1]?.messages);
    assert.deepEqual(addDemoReply(after, "DEMO-1042", " "), after);
    assert.deepEqual(addDemoReply(after, "DEMO-1042", "a".repeat(501)), after);
  });

  it("filters by status and text without changing the data", () => {
    const cases = freshDemoCases();
    assert.equal(filterDemoCases(cases, "waiting", "").length, 3);
    assert.equal(filterDemoCases(cases, "assigned", "").length, 2);
    assert.equal(filterDemoCases(cases, "resolved", "").length, 1);
    assert.deepEqual(filterDemoCases(cases, "all", "  QUOTATION  ").map((item) => item.id), ["DEMO-1043"]);
    assert.deepEqual(filterDemoCases(cases, "all", "demo-1045").map((item) => item.id), ["DEMO-1045"]);
    assert.equal(cases.length, 6);
  });
});
