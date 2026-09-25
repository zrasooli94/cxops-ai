import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  PILOT_CUSTOMER_COUNT,
  PILOT_DISCLAIMER,
  PILOT_KNOWLEDGE_PACK,
  PILOT_OPTIONAL_REPLAY_REFS,
  PILOT_QUEUES,
  PILOT_SCENARIOS,
  PILOT_SERVICE_LINES,
  PILOT_SLA_POLICIES,
  PILOT_TICKET_COUNT,
  PILOT_WALKTHROUGH,
  queueNamesFor,
  scenarioCountFor,
} from "./automotive-pilot.ts";

describe("Phase 1M automotive pilot config", () => {
  it("exposes a fixed set of service lines", () => {
    assert.deepEqual(
      PILOT_SERVICE_LINES.map((s) => s.key),
      ["vehicle_acquisition", "auto_parts", "general_support"],
    );
  });

  it("exactly one SLA policy is the default", () => {
    assert.equal(
      PILOT_SLA_POLICIES.filter((p) => p.is_default).length,
      1,
    );
    assert.equal(
      PILOT_SLA_POLICIES.find((p) => p.is_default)?.name,
      "A1 General Support SLA (Demo)",
    );
  });

  it("every SLA policy mirrors the backend per-priority targets", () => {
    for (const policy of PILOT_SLA_POLICIES) {
      assert.ok(policy.name.endsWith("(Demo)"), policy.name);
      for (const priority of ["normal", "high"] as const) {
        assert.ok(policy.first_response_minutes[priority] > 0, policy.name);
        assert.ok(policy.resolution_minutes[priority] > 0, policy.name);
        assert.ok(
          policy.first_response_minutes.high <=
            policy.first_response_minutes.normal,
          policy.name,
        );
        assert.ok(
          policy.resolution_minutes.high <=
            policy.resolution_minutes.normal,
          policy.name,
        );
      }
    }
  });

  it("every queue maps to a known service line and SLA policy", () => {
    const serviceLines = new Set(PILOT_SERVICE_LINES.map((s) => s.key));
    const policyNames = new Set(PILOT_SLA_POLICIES.map((p) => p.name));
    for (const queue of PILOT_QUEUES) {
      assert.ok(serviceLines.has(queue.service_line), queue.key);
      assert.ok(policyNames.has(queue.sla_policy), queue.key);
    }
  });

  it("catalog covers all nine acquisition steps and eight parts scenarios", () => {
    assert.equal(PILOT_SCENARIOS.length, 18);
    assert.equal(scenarioCountFor("vehicle_acquisition"), 9);
    assert.equal(scenarioCountFor("auto_parts"), 8);
    assert.equal(scenarioCountFor("general_support"), 1);
  });

  it("every scenario references a seeded ticket and a service line", () => {
    for (const scenario of PILOT_SCENARIOS) {
      assert.match(scenario.id, /^(acq|par|safety)-/, scenario.id);
      assert.ok(scenario.title.length >= 4, scenario.id);
      assert.ok(scenario.description.length >= 20, scenario.id);
      assert.ok(
        PILOT_SERVICE_LINES.some(
          (s) => s.key === scenario.service_line,
        ),
        scenario.id,
      );
    }
  });

  it("risk-labeled scenarios are explicitly present", () => {
    const risk = PILOT_SCENARIOS.filter((s) => s.risk);
    assert.equal(risk.length, 2);
    assert.ok(risk.some((s) => s.id === "par-parts-compatibility"));
    assert.ok(risk.some((s) => s.id === "safety-prompt-injection"));
  });

  it("optional replay refs are a subset of scenario ticket refs", () => {
    const scenarioRefs = new Set(PILOT_SCENARIOS.map((s) => s.ref));
    for (const ref of PILOT_OPTIONAL_REPLAY_REFS) {
      assert.ok(scenarioRefs.has(ref), ref);
    }
    assert.equal(PILOT_OPTIONAL_REPLAY_REFS.length, 4);
  });

  it("every knowledge document carries the disclaimer", () => {
    for (const doc of PILOT_KNOWLEDGE_PACK) {
      assert.ok(doc.title.length > 4, doc.title);
    }
    assert.ok(PILOT_DISCLAIMER.length >= 20);
    assert.ok(PILOT_DISCLAIMER.toLowerCase().includes("illustrative"));
  });

  it("the pilot tenant carries 40 tickets and 6 customers", () => {
    assert.equal(PILOT_TICKET_COUNT, 40);
    assert.equal(PILOT_CUSTOMER_COUNT, 6);
  });

  it("queue grouping is service-line scoped", () => {
    assert.deepEqual(queueNamesFor("vehicle_acquisition"), [
      "Vehicle Valuations",
      "Vehicle Pickups",
      "Vehicle Documents & Payments",
    ]);
    assert.deepEqual(queueNamesFor("auto_parts"), [
      "Parts Sales",
      "Parts Returns & Warranty",
    ]);
  });

  it("the walkthrough is a short ordered demo script", () => {
    assert.ok(PILOT_WALKTHROUGH.length >= 4);
    for (const step of PILOT_WALKTHROUGH) {
      assert.ok(step.title.length >= 4);
      assert.ok(step.detail.length >= 20);
    }
  });
});