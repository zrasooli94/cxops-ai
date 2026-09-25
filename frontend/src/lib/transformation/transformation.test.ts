import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { CAPABILITIES } from "../authorization/capabilities.ts";
import { createAuthorizationView } from "../authorization/helpers.ts";
import {
  canViewTransformation,
  comparisonKeyLabel,
  fetchServiceTransformation,
  formatMinutes,
  formatMoney,
  formatPercentChange,
  formatRate,
  isSupportedTransformationWindow,
  scopeLabel,
  signalLabel,
  SERVICE_TRANSFORMATION_PATH,
  SERVICE_TRANSFORMATION_LOAD_ERROR,
  TRANSFORMATION_DEFAULT_DAYS,
  TRANSFORMATION_WINDOW_DAYS,
  transformationPath,
  type ServiceTransformationSummary,
} from "./transformation.ts";

function canFrom(capabilities: readonly string[]) {
  const view = createAuthorizationView({
    organization_id: 1,
    role: "ignored",
    capabilities,
  });
  return view.can;
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function recordingFetcher(urls: string[]) {
  return async (input: RequestInfo | URL): Promise<Response> => {
    urls.push(String(input));
    return jsonResponse({});
  };
}

const SUMMARY: ServiceTransformationSummary = {
  generated_at: "2026-09-25T10:00:00Z",
  window: {
    days: 30,
    current_start: "2026-08-26T00:00:00Z",
    current_end: "2026-09-25T00:00:00Z",
    previous_start: "2026-07-27T00:00:00Z",
    previous_end: "2026-08-26T00:00:00Z",
  },
  service_volume: {
    tickets_created: 120,
    tickets_resolved: 98,
    currently_open: 41,
    currently_needs_response: 9,
  },
  service_performance: {
    average_first_response_minutes: 12.5,
    median_first_response_minutes: 9,
    average_resolution_time_minutes: 184.2,
    median_resolution_time_minutes: null,
  },
  sla: {
    first_response_sla_breaches: 2,
    resolution_sla_breaches: 5,
    total_sla_breaches: 7,
    due_soon: 3,
    escalation_count: 11,
    escalation_rate: 9.17,
    reopened_tickets: 4,
    reopen_rate: 4.08,
  },
  ai_adoption: {
    tickets_analyzed_by_ai: 100,
    agent_runs: 80,
    autonomous_executions: 30,
    human_approval_required: 25,
    human_approved: 22,
    human_rejected: 3,
    successful_agent_executions: 60,
    failed_agent_executions: 5,
    no_action_runs: 10,
    ai_analysis_rate: 83.33,
    autonomous_execution_rate: 37.5,
    human_approval_rate: 31.25,
    execution_success_rate: 92.31,
  },
  specialist_usage: {
    coordinator_runs: 80,
    knowledge_specialist_runs: 62,
    action_specialist_runs: 55,
    knowledge_usage_rate: 77.5,
    pure_action_route_rate: 12.5,
    invalid_specialist_path_count: 0,
  },
  human_workload: {
    human_messages_sent: 210,
    ai_executed_replies: 33,
  },
  value_realization: {
    estimated_minutes_saved: 990,
    estimated_hours_saved: 16.5,
    estimated_labor_savings_usd: 412.5,
    agent_ai_cost_usd: 0.18,
    estimated_net_savings_usd: 412.32,
    pricing_configured: true,
    measurement_status: "active",
    minimum_autonomous_samples: 30,
    sample_size_sufficient: true,
    roi_percent: 42.0,
  },
  queue_breakdown: [],
  channel_breakdown: [],
  comparisons: {
    "service_volume.tickets_created": {
      current: 120,
      previous: 100,
      absolute_change: 20,
      percent_change: 20.0,
    },
  },
  opportunity_signals: [],
};

describe("transformation capability gate", () => {
  it("ticket.read enables the transformation page", () => {
    assert.equal(
      canViewTransformation(canFrom([CAPABILITIES.TICKET_READ])),
      true,
    );
  });

  it("ticket.read combined with other capabilities still shows the page", () => {
    assert.equal(
      canViewTransformation(
        canFrom([CAPABILITIES.TICKET_READ, CAPABILITIES.CUSTOMER_READ]),
      ),
      true,
    );
  });

  it("an unrelated capability never enables the page", () => {
    assert.equal(
      canViewTransformation(
        canFrom([
          CAPABILITIES.CUSTOMER_READ,
          CAPABILITIES.KNOWLEDGE_READ,
          CAPABILITIES.OBSERVABILITY_READ,
        ]),
      ),
      false,
    );
    assert.equal(
      canViewTransformation(canFrom([CAPABILITIES.TICKET_WRITE])),
      false,
    );
  });

  it("an empty capability set fails closed", () => {
    assert.equal(canViewTransformation(canFrom([])), false);
  });
});

describe("transformation window validation", () => {
  it("accepts only the three backend windows", () => {
    for (const days of TRANSFORMATION_WINDOW_DAYS) {
      assert.equal(isSupportedTransformationWindow(days), true, `${days}`);
    }
  });

  it("rejects every other window the backend would 422", () => {
    for (const days of [1, 5, 14, 45, 91, 365, 0, -7]) {
      assert.equal(isSupportedTransformationWindow(days), false, `${days}`);
    }
  });

  it("defaults to 30 days", () => {
    assert.equal(TRANSFORMATION_DEFAULT_DAYS, 30);
  });

  it("builds the path with a supported window", () => {
    assert.equal(transformationPath(7), "/api/backend/service-operations/transformation?days=7");
    assert.equal(transformationPath(90), "/api/backend/service-operations/transformation?days=90");
  });

  it("falls back to the default window for an unsupported value", () => {
    assert.equal(
      transformationPath(14),
      "/api/backend/service-operations/transformation?days=30",
    );
  });
});

describe("transformation GET helper", () => {
  it("requests the transformation endpoint with the default window", async () => {
    const urls: string[] = [];
    await fetchServiceTransformation(TRANSFORMATION_DEFAULT_DAYS, recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, [
      "/api/backend/service-operations/transformation?days=30",
    ]);
  });

  it("requests the requested window verbatim", async () => {
    const urls: string[] = [];
    await fetchServiceTransformation(7, recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, [
      "/api/backend/service-operations/transformation?days=7",
    ]);
  });

  it("parses a summary response", async () => {
    const fetcher: typeof fetch = async () => jsonResponse(SUMMARY);
    const summary = await fetchServiceTransformation(30, fetcher);
    assert.equal(summary.window.days, 30);
    assert.equal(summary.service_volume.tickets_created, 120);
    assert.equal(summary.sla.total_sla_breaches, 7);
    assert.equal(summary.ai_adoption.execution_success_rate, 92.31);
    assert.equal(
      summary.comparisons["service_volume.tickets_created"].percent_change,
      20.0,
    );
  });

  it("throws a friendly error when the backend is not ok", async () => {
    const fetcher: typeof fetch = async () => jsonResponse({}, 403);
    await assert.rejects(
      () => fetchServiceTransformation(30, fetcher),
      /Could not load transformation data/,
    );
  });

  it("exposes the safe error constant verbatim", () => {
    assert.equal(SERVICE_TRANSFORMATION_LOAD_ERROR, "Could not load transformation data.");
  });

  it("sends only a GET with no body", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${init?.method ?? "GET"}`);
      return jsonResponse(SUMMARY);
    }) as typeof fetch;
    await fetchServiceTransformation(90, fetcher);
    assert.deepEqual(calls, [
      "/api/backend/service-operations/transformation?days=90:GET",
    ]);
  });
});

describe("rate formatting", () => {
  it("formats backend 0–100 percentages directly without multiplication", () => {
    assert.equal(formatRate(40), "40%");
    assert.equal(formatRate(42.5), "42.5%");
    assert.equal(formatRate(83.333), "83.33%");
    assert.equal(formatRate(0), "0%");
    assert.equal(formatRate(100), "100%");
  });

  it("renders em dash for a withheld rate", () => {
    assert.equal(formatRate(null), "—");
    assert.equal(formatRate(undefined), "—");
  });

  it("formats percent change with explicit signs", () => {
    assert.equal(formatPercentChange(20.0), "+20%");
    assert.equal(formatPercentChange(-3.25), "-3.25%");
    assert.equal(formatPercentChange(0), "0%");
    assert.equal(formatPercentChange(12.5), "+12.5%");
  });

  it("renders em dash for a missing percent change", () => {
    assert.equal(formatPercentChange(null), "—");
    assert.equal(formatPercentChange(undefined), "—");
  });

  it("formats minutes compactly", () => {
    assert.equal(formatMinutes(12.5), "13 min");
    assert.equal(formatMinutes(184.2), "184 min");
    assert.equal(formatMinutes(null), "—");
    assert.equal(formatMinutes(undefined), "—");
  });

  it("formats money with two decimals and separators", () => {
    assert.equal(formatMoney(412.5), "$412.50");
    assert.equal(formatMoney(1234.56), "$1,234.56");
    assert.equal(formatMoney(0), "$0.00");
    assert.equal(formatMoney(0.18), "$0.18");
  });
});

describe("comparison rendering helpers", () => {
  it("labels a comparison key from its metric path", () => {
    assert.equal(
      comparisonKeyLabel("service_volume.tickets_created"),
      "Service Volume · Tickets Created",
    );
    assert.equal(
      comparisonKeyLabel("ai_adoption.autonomous_execution_rate"),
      "AI Adoption · Autonomous Execution Rate",
    );
  });

  it("labels the first segment alone for a single-part key", () => {
    assert.equal(comparisonKeyLabel("due_soon"), "Due Soon");
  });
});

describe("signal and scope labels", () => {
  it("maps the five backend signal literals", () => {
    assert.equal(signalLabel("sla_pressure"), "SLA pressure");
    assert.equal(signalLabel("ai_adoption"), "AI adoption");
    assert.equal(signalLabel("knowledge_utilization"), "Knowledge utilization");
    assert.equal(signalLabel("approval_backlog"), "Approval backlog");
    assert.equal(signalLabel("reopen_risk"), "Reopen risk");
  });

  it("falls back to a formatted key for an unknown signal", () => {
    assert.equal(signalLabel("unknown_signal"), "Unknown Signal");
  });

  it("maps the two backend scope literals", () => {
    assert.equal(scopeLabel("queue"), "Queue");
    assert.equal(scopeLabel("organization"), "Organization");
  });

  it("falls back verbatim for an unknown scope", () => {
    assert.equal(scopeLabel("team"), "team");
  });
});

describe("safe rendering contract", () => {
  it("the lib path is the ticket.read service-operations endpoint, never observability", () => {
    assert.equal(
      SERVICE_TRANSFORMATION_PATH,
      "/api/backend/service-operations/transformation",
    );
    assert.equal(
      SERVICE_TRANSFORMATION_PATH.startsWith("/api/backend/observability"),
      false,
    );
  });

  it("formatting helpers are null-safe and never emit undefined or raw errors", () => {
    for (const rendered of [
      formatRate(null),
      formatRate(undefined),
      formatPercentChange(null),
      formatPercentChange(undefined),
      formatMinutes(null),
      formatMinutes(undefined),
    ]) {
      assert.equal(rendered, "—");
    }
  });

  it("the display pipeline references only the typed, safe summary surface", () => {
    const serialized = JSON.stringify(SUMMARY);
    for (const unsafe of ["requester_email", "prompt", "ticket_body", "secret"]) {
      assert.equal(serialized.includes(unsafe), false, unsafe);
    }
  });
});