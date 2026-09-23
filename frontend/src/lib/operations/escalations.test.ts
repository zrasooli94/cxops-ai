import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  automationActionSummary,
  buildEscalationSummaryCards,
  escalationStageVariant,
  formatDateTime,
  SLA_EVENT_TYPES,
  validateAutomationRule,
  VALID_PRIORITIES,
} from "./escalations.ts";

describe("escalation stage badge mapping", () => {
  it("1: breached maps to danger", () => {
    assert.equal(escalationStageVariant("breached"), "danger");
  });

  it("2: due_soon maps to warning", () => {
    assert.equal(escalationStageVariant("due_soon"), "warning");
  });

  it("3: acknowledged maps to info", () => {
    assert.equal(escalationStageVariant("acknowledged"), "info");
  });

  it("4: resolved maps to success", () => {
    assert.equal(escalationStageVariant("resolved"), "success");
  });

  it("5: open and unknown stages map to default", () => {
    assert.equal(escalationStageVariant("open"), "default");
    assert.equal(escalationStageVariant("unknown"), "default");
  });
});

describe("escalation summary cards", () => {
  it("6: builds cards in display order", () => {
    const summary = {
      total: 10,
      active: 6,
      unacknowledged: 3,
      due_soon: 2,
      breached: 1,
    };

    const cards = buildEscalationSummaryCards(summary);

    assert.deepEqual(cards, [
      { label: "Total", value: 10 },
      { label: "Active", value: 6 },
      { label: "Unacknowledged", value: 3 },
      { label: "Due soon", value: 2 },
      { label: "Breached", value: 1 },
    ]);
  });
});

describe("date-time formatting", () => {
  it("7: formats valid ISO timestamps", () => {
    const formatted = formatDateTime("2026-09-22T14:30:00.000Z");
    assert.ok(formatted.length > 0);
    assert.notEqual(formatted, "—");
  });

  it("8: returns placeholder for null or invalid inputs", () => {
    assert.equal(formatDateTime(null), "—");
    assert.equal(formatDateTime(undefined), "—");
    assert.equal(formatDateTime("not-a-date"), "—");
  });
});

describe("SLA automation rule validation", () => {
  const baseRule = {
    name: "Urgent on breach",
    event_type: "sla.resolution.breached",
    enabled: true,
    actions: { priority: "urgent" },
  } as const;

  it("9: accepts a valid rule with a priority action", () => {
    assert.equal(validateAutomationRule(baseRule), null);
  });

  it("10: accepts a valid rule with a queue key action", () => {
    assert.equal(
      validateAutomationRule({
        ...baseRule,
        actions: { service_queue_key: "tier-2" },
      }),
      null,
    );
  });

  it("11: rejects a missing name", () => {
    const result = validateAutomationRule({ ...baseRule, name: "  " });
    assert.equal(result, "Rule name is required.");
  });

  it("12: rejects an invalid event type", () => {
    const result = validateAutomationRule({
      ...baseRule,
      event_type: "ticket.created",
    });
    assert.equal(result, "Select a valid SLA escalation event type.");
  });

  it("13: rejects invalid priority values", () => {
    const result = validateAutomationRule({
      ...baseRule,
      actions: { priority: "critical" },
    });
    assert.equal(
      result,
      `Priority must be one of: ${VALID_PRIORITIES.join(", ")}.`,
    );
  });

  it("14: rejects a whitespace-only queue key", () => {
    const result = validateAutomationRule({
      ...baseRule,
      actions: { service_queue_key: "   " },
    });
    assert.equal(result, "Queue key cannot be empty.");
  });

  it("15: rejects a rule with no actions", () => {
    const result = validateAutomationRule({ ...baseRule, actions: {} });
    assert.equal(
      result,
      "At least one action (priority, queue key, or category) is required.",
    );
  });

  it("16: accepts a rule with a category action", () => {
    assert.equal(
      validateAutomationRule({
        ...baseRule,
        actions: { category: "escalated" },
      }),
      null,
    );
  });
});

describe("automation action summary", () => {
  it("17: summarizes a single action", () => {
    assert.equal(
      automationActionSummary({ priority: "high" }),
      "priority → high",
    );
  });

  it("18: summarizes multiple actions", () => {
    assert.equal(
      automationActionSummary({
        priority: "urgent",
        service_queue_key: "escalations",
        category: "sla",
      }),
      "priority → urgent, queue → escalations, category → sla",
    );
  });

  it("19: reports missing actions", () => {
    assert.equal(automationActionSummary({}), "No actions configured");
  });
});

describe("SLA automation event types", () => {
  it("20: contains the four escalation event types", () => {
    assert.deepEqual(SLA_EVENT_TYPES, [
      "sla.first_response.due_soon",
      "sla.first_response.breached",
      "sla.resolution.due_soon",
      "sla.resolution.breached",
    ]);
  });
});
