import { describe, it } from "node:test";

import assert from "node:assert/strict";

import { CAPABILITIES } from "../authorization/capabilities.ts";
import {
  EXPERIMENT_ACTIONS_BY_STATUS,
  EXPERIMENT_CAUSALITY_DISCLAIMER,
  EXPERIMENT_PATH,
  EXPERIMENT_SCOPE_QUEUE,
  EXPERIMENT_STATUS_ARCHIVED,
  EXPERIMENT_STATUS_COMPLETED,
  EXPERIMENT_STATUS_CANCELLED,
  EXPERIMENT_STATUS_DRAFT,
  EXPERIMENT_STATUS_READY,
  EXPERIMENT_STATUS_RUNNING,
  ExperimentRequestError,
  buildExperimentCreatePayload,
  buildExperimentExecutiveSummary,
  buildExperimentNarrative,
  canManageExperiments,
  canViewExperiments,
  coreExperimentValidationError,
  createExperiment,
  deriveExperimentExperience,
  experimentActions,
  experimentMeasurementStatusLabel,
  experimentScopeLabel,
  experimentStatusHint,
  experimentStatusLabel,
  experimentTargetMetricsError,
  fetchExperiment,
  fetchExperimentList,
  fetchServiceQueues,
  formatMetricCell,
  formatMetricDelta,
  formatMetricDeltaWords,
  formatPp,
  isFiniteExperimentValue,
  runExperimentAction,
  toIsoDateTime,
  type ServiceTransformationExperiment,
  type ServiceTransformationExperimentCreate,
} from "./experiments.ts";

function orgExperiment(
  overrides: Partial<ServiceTransformationExperiment> = {},
): ServiceTransformationExperiment {
  return {
    id: 1,
    organization_id: 10,
    name: "Autonomous dispatch",
    description: null,
    scope_type: "organization",
    scope_key: null,
    baseline_window_days: 30,
    measurement_window_days: 30,
    hypothesis: { summary: "Roll out autonomous dispatch.", change_description: null, expected_direction: null },
    target_metrics: { autonomous_execution_rate: 40 },
    source_scenario_id: null,
    source_scenario_snapshot: null,
    planned_start_at: null,
    planned_end_at: null,
    status: EXPERIMENT_STATUS_DRAFT,
    baseline_captured_at: null,
    baseline_snapshot: null,
    actual_started_at: null,
    actual_ended_at: null,
    completion_summary: null,
    measured_at: null,
    observed_outcome: null,
    outcome_comparison: null,
    measurement_status: null,
    comparison_version: null,
    archiving_reason: null,
    archived_at: null,
    created_by_subject: "alice",
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

function jsonResponse(
  body: unknown,
  init: { status?: number; ok?: boolean } = {},
): Response {
  const { status = 200, ok = status >= 200 && status < 300 } = init;
  return {
    ok,
    status,
    statusText: "",
    headers: new Headers(),
    json: async () => body,
  } as Response;
}

function stubFetch(response: Response): typeof fetch {
  return (async () => response) as typeof fetch;
}

describe("capability gates", () => {
  it("read requires transformation.experiment.read", () => {
    const allows = (capability: string) => capability === "other";
    assert.equal(canViewExperiments(() => false), false);
    assert.equal(canViewExperiments(() => true), true);
    assert.equal(canViewExperiments(allows), false);
  });

  it("manage requires transformation.experiment.manage", () => {
    const allows = (capability: string) => capability === "other";
    assert.equal(canManageExperiments(() => false), false);
    assert.equal(canManageExperiments(() => true), true);
    assert.equal(canManageExperiments((capability) => capability === CAPABILITIES.TRANSFORMATION_EXPERIMENT_READ), false);
    assert.equal(canManageExperiments(allows), false);
  });

  it("read-only derives when only read is granted", () => {
    const can = (capability: string) =>
      capability === CAPABILITIES.TRANSFORMATION_EXPERIMENT_READ;
    const experience = deriveExperimentExperience(can);
    assert.equal(experience.canRead, true);
    assert.equal(experience.canManage, false);
    assert.equal(experience.readOnly, true);
  });

  it("manager is not read-only", () => {
    const can = (capability: string) =>
      capability === CAPABILITIES.TRANSFORMATION_EXPERIMENT_READ ||
      capability === CAPABILITIES.TRANSFORMATION_EXPERIMENT_MANAGE;
    assert.equal(deriveExperimentExperience(can).readOnly, false);
  });
});

describe("validation", () => {
  it("blank name and hypothesis are rejected", () => {
    assert.equal(
      coreExperimentValidationError({ name: "  ", summary: "ok", targetMetrics: { roi_percent: 5 }, scopeType: "organization" }),
      "Experiment name cannot be blank.",
    );
    assert.equal(
      coreExperimentValidationError({ name: "ok", summary: "  ", targetMetrics: { roi_percent: 5 }, scopeType: "organization" }),
      "Hypothesis summary cannot be blank.",
    );
  });

  it("no target metrics is rejected", () => {
    assert.equal(
      experimentTargetMetricsError({}, "organization"),
      "Select at least one target metric.",
    );
  });

  it("rate targets must be 0-100 percentages", () => {
    assert.equal(isFiniteExperimentValue("autonomous_execution_rate", 40), true);
    assert.equal(isFiniteExperimentValue("autonomous_execution_rate", 100), true);
    assert.equal(isFiniteExperimentValue("autonomous_execution_rate", 0), true);
    assert.equal(isFiniteExperimentValue("autonomous_execution_rate", 101), false);
    assert.equal(isFiniteExperimentValue("autonomous_execution_rate", -1), false);
  });

  it("roi target may be negative like the backend kind allows", () => {
    assert.equal(isFiniteExperimentValue("roi_percent", -12), true);
    assert.equal(isFiniteExperimentValue("estimated_net_savings_usd", -1), false);
  });

  it("queue scope rejects value and ROI targets", () => {
    assert.equal(
      experimentTargetMetricsError(
        { autonomous_execution_rate: 40, estimated_net_savings_usd: 1000 },
        EXPERIMENT_SCOPE_QUEUE,
      ),
      "Value and ROI targets are not available for queue-scoped experiments.",
    );
    assert.equal(
      experimentTargetMetricsError(
        { autonomous_execution_rate: 40 },
        EXPERIMENT_SCOPE_QUEUE,
      ),
      null,
    );
  });
});

describe("create payload", () => {
  it("never includes tenant, baseline, observed, or measurement status fields", () => {
    const payload = buildExperimentCreatePayload({
      name: "Rollout",
      description: "desc",
      scopeType: "organization",
      scopeKey: null,
      baselineWindowDays: 30,
      measurementWindowDays: 90,
      plannedStartAt: null,
      plannedEndAt: null,
      hypothesisSummary: "Roll out autonomous dispatch.",
      changeDescription: "Enable autonomous dispatch.",
      expectedDirection: { autonomous_execution_rate: "increase" },
      targetMetrics: { autonomous_execution_rate: 40 },
      sourceScenarioId: null,
    });

    const forbidden = [
      "organization_id",
      "baseline_snapshot",
      "observed_outcome",
      "outcome_comparison",
      "measurement_status",
      "comparison_version",
    ];
    for (const key of forbidden) {
      assert.equal(
        key in payload,
        false,
        `create payload must never carry ${key}`,
      );
    }
    assert.equal(payload.scope_key, null);
  });

  it("expected direction is omitted when none set", () => {
    const payload = buildExperimentCreatePayload({
      name: "Rollout",
      description: null,
      scopeType: "organization",
      scopeKey: null,
      baselineWindowDays: 30,
      measurementWindowDays: 30,
      plannedStartAt: null,
      plannedEndAt: null,
      hypothesisSummary: "Roll out autonomous dispatch.",
      changeDescription: null,
      expectedDirection: {},
      targetMetrics: { autonomous_execution_rate: 40 },
      sourceScenarioId: null,
    });
    assert.equal(payload.hypothesis.expected_direction, null);
  });

  it("invalid directions are dropped", () => {
    const payload = buildExperimentCreatePayload({
      name: "Rollout",
      description: null,
      scopeType: "organization",
      scopeKey: null,
      baselineWindowDays: 30,
      measurementWindowDays: 30,
      plannedStartAt: null,
      plannedEndAt: null,
      hypothesisSummary: "Summary.",
      changeDescription: null,
      expectedDirection: {
        autonomous_execution_rate: "increase",
        reopen_rate: "sideways",
      },
      targetMetrics: { autonomous_execution_rate: 40, reopen_rate: 5 },
      sourceScenarioId: null,
    });
    assert.deepEqual(payload.hypothesis.expected_direction, {
      autonomous_execution_rate: "increase",
    });
  });

  it("queue scope requires a key and carries value target into queue rejection", () => {
    const payload = buildExperimentCreatePayload({
      name: "Queue trial",
      description: null,
      scopeType: EXPERIMENT_SCOPE_QUEUE,
      scopeKey: "billing",
      baselineWindowDays: 7,
      measurementWindowDays: 7,
      plannedStartAt: null,
      plannedEndAt: null,
      hypothesisSummary: "Trial on billing queue.",
      changeDescription: null,
      expectedDirection: {},
      targetMetrics: { human_approval_rate: 30 },
      sourceScenarioId: 12,
    });
    assert.equal(payload.scope_key, "billing");
    assert.equal(payload.source_scenario_id, 12);
  });

  it("unsupported windows fall back to 30 days", () => {
    const payload = buildExperimentCreatePayload({
      name: "Rollout",
      description: null,
      scopeType: "organization",
      scopeKey: null,
      baselineWindowDays: 45,
      measurementWindowDays: 10,
      plannedStartAt: null,
      plannedEndAt: null,
      hypothesisSummary: "Summary.",
      changeDescription: null,
      expectedDirection: { autonomous_execution_rate: "increase" },
      targetMetrics: { autonomous_execution_rate: 40 },
      sourceScenarioId: null,
    });
    assert.equal(payload.baseline_window_days, 30);
    assert.equal(payload.measurement_window_days, 30);
  });

  it("datetime-local values convert to ISO timestamps", () => {
    const iso = toIsoDateTime("2026-10-01T09:00");
    assert.ok(iso !== null && /^\d{4}-\d{2}-\d{2}T/.test(iso));
    assert.equal(toIsoDateTime(""), null);
    assert.equal(toIsoDateTime(undefined), null);
  });
});

describe("lifecycle", () => {
  it("actions match the backend state machine exactly", () => {
    assert.deepEqual(experimentActions(EXPERIMENT_STATUS_DRAFT), [
      "capture-baseline",
      "cancel",
      "archive",
    ]);
    assert.deepEqual(experimentActions(EXPERIMENT_STATUS_READY), [
      "start",
      "cancel",
      "archive",
    ]);
    assert.deepEqual(experimentActions(EXPERIMENT_STATUS_RUNNING), [
      "complete",
      "cancel",
    ]);
    assert.deepEqual(experimentActions(EXPERIMENT_STATUS_COMPLETED), ["archive"]);
    assert.deepEqual(experimentActions(EXPERIMENT_STATUS_CANCELLED), ["archive"]);
    assert.deepEqual(experimentActions(EXPERIMENT_STATUS_ARCHIVED), []);
    assert.deepEqual(EXPERIMENT_ACTIONS_BY_STATUS["unknown"], undefined);
  });

  it("archive from a running experiment is not offered (backend 409)", () => {
    assert.equal(experimentActions(EXPERIMENT_STATUS_RUNNING).includes("archive"), false);
  });
});

describe("formatters", () => {
  it("rates render on the 0-100 scale without rescaling", () => {
    assert.equal(formatMetricCell(35, "rate"), "35%");
    assert.equal(formatMetricCell(40, "rate"), "40%");
    assert.equal(formatMetricCell(null, "rate"), "—");
  });

  it("percentage-point differences are never relabelled as percentages", () => {
    assert.equal(formatPp(-3), "-3.0 pp");
    assert.equal(formatPp(11), "+11.0 pp");
    assert.equal(formatPp(0), "0.0 pp");
    assert.equal(formatPp(12.34), "+12.3 pp");
    assert.equal(formatPp(null), "—");
    assert.equal(formatMetricDelta(-3, "percentage_points"), "-3.0 pp");
  });

  it("minute deltas are minutes, not pp", () => {
    assert.equal(formatMetricDelta(-12, "minutes"), "-12 min");
  });

  it("USD deltas and amounts render with signs and thousands separators", () => {
    assert.equal(formatMetricDelta(120.5, "usd"), "+$120.5");
    assert.equal(formatMetricCell(1200, "amount"), "$1,200.00");
  });

  it("delta words are used only for narrative sentences", () => {
    assert.equal(
      formatMetricDeltaWords(11, "percentage_points"),
      "+11 percentage points",
    );
    assert.equal(
      formatMetricDeltaWords(-5, "percentage_points"),
      "-5 percentage points",
    );
    assert.equal(formatMetricDeltaWords(-45, "minutes"), "-45 min");
  });
});

describe("detail presentation", () => {
  it("scope label renders organization and queue verbatim keys", () => {
    assert.equal(experimentScopeLabel("organization", null), "Organization");
    assert.equal(experimentScopeLabel("queue", "billing"), "Queue: billing");
  });

  it("status labels and hints cover every backend status", () => {
    assert.deepEqual(
      [EXPERIMENT_STATUS_DRAFT, EXPERIMENT_STATUS_READY, EXPERIMENT_STATUS_RUNNING, EXPERIMENT_STATUS_COMPLETED, EXPERIMENT_STATUS_CANCELLED, EXPERIMENT_STATUS_ARCHIVED].map(experimentStatusLabel),
      ["Draft", "Ready", "Running", "Completed", "Cancelled", "Archived"],
    );
    assert.equal(
      experimentStatusHint(EXPERIMENT_STATUS_DRAFT),
      "Capture a baseline before starting.",
    );
    assert.equal(
      experimentStatusHint(EXPERIMENT_STATUS_READY),
      "Baseline captured. This experiment is ready to start.",
    );
    assert.equal(
      experimentStatusHint(EXPERIMENT_STATUS_RUNNING),
      "Measurement window is in progress.",
    );
    assert.equal(
      experimentStatusHint(EXPERIMENT_STATUS_COMPLETED),
      "Observed outcome has been measured.",
    );
  });

  it("measurement status labels are declared, never invented", () => {
    assert.equal(experimentMeasurementStatusLabel("measured"), "Measured");
    assert.equal(
      experimentMeasurementStatusLabel("insufficient_sample"),
      "Insufficient sample",
    );
    assert.equal(experimentMeasurementStatusLabel("not_a_status"), "not_a_status");
  });
});

describe("executive summary", () => {
  it("rejects recomputation: renders only backend comparison cells", () => {
    const experiment = orgExperiment({
      status: EXPERIMENT_STATUS_COMPLETED,
      measurement_status: "measured",
      comparison_version: "1.4.0",
      measured_at: "2026-10-01T12:00:00Z",
      outcome_comparison: {
        comparison_version: "1.4.0",
        measurement_status: "measured",
        window: { start: "2026-09-01T00:00:00Z", end: "2026-10-01T00:00:00Z", days: 30 },
        metrics: [
          {
            metric: "autonomous_execution_rate",
            baseline_value: 24,
            target_value: 40,
            observed_value: 35,
            change_from_baseline: 11,
            variance_from_target: -5,
            projected_value: null,
            variance_from_projection: null,
            unit: "percentage_points",
            direction_vs_baseline: "increased",
            direction_vs_target: "decreased",
            direction_vs_projection: null,
            measurement_status: "measured",
            warning: null,
          },
        ],
        warnings: [],
        limitations: [EXPERIMENT_CAUSALITY_DISCLAIMER],
      },
    });

    const summary = buildExperimentExecutiveSummary(experiment);
    assert.equal(summary.comparisonRows.length, 1);
    const row = summary.comparisonRows[0];
    assert.equal(row.baseline, "24%");
    assert.equal(row.target, "40%");
    assert.equal(row.observed, "35%");
    assert.equal(row.changeFromBaseline, "+11.0 pp");
    assert.equal(row.varianceFromTarget, "-5.0 pp");
    assert.equal(row.projected, null);
    assert.equal(summary.measurementStatusLabel, "Measured");
    assert.equal(summary.comparisonVersion, "1.4.0");
  });

  it("narrative speaks in percentage points and never claims causation", () => {
    const experiment = orgExperiment({
      status: EXPERIMENT_STATUS_COMPLETED,
      measurement_status: "measured",
      outcome_comparison: {
        comparison_version: "1.4.0",
        measurement_status: "measured",
        window: { start: "2026-09-01T00:00:00Z", end: "2026-10-01T00:00:00Z", days: 30 },
        metrics: [
          {
            metric: "autonomous_execution_rate",
            baseline_value: 24,
            target_value: 40,
            observed_value: 35,
            change_from_baseline: 11,
            variance_from_target: -5,
            projected_value: 38,
            variance_from_projection: -3,
            unit: "percentage_points",
            direction_vs_baseline: "increased",
            direction_vs_target: "decreased",
            direction_vs_projection: "decreased",
            measurement_status: "measured",
            warning: null,
          },
        ],
        warnings: [],
        limitations: [EXPERIMENT_CAUSALITY_DISCLAIMER],
      },
    });

    const narrative = buildExperimentNarrative(experiment);
    assert.deepEqual(narrative, [
      "Autonomous execution was 24% at baseline, the target was 40%, and the observed rate was 35%.",
      "The observed value was +11 percentage points from baseline and -5 percentage points from target.",
      "The scenario projection was 38%; the observed value was -3 percentage points from the projection.",
    ]);

    const forbidden = [
      /caus(ed|e)/i,
      /\bbecause\b/i,
      /led to/i,
      /\boutperformed\b/i,
      /\bimproved\b/i,
      /recommend/i,
      /success/i,
      /failure/i,
      /\bwin(ner|s)\b/i,
      /\blost\b/i,
      /better than|worse than/i,
    ];
    for (const pattern of forbidden) {
      for (const sentence of narrative) {
        assert.equal(
          pattern.test(sentence),
          false,
          `narrative must not contain ${pattern} but had "${sentence}"`,
        );
      }
    }
  });

  it("value rows come only from backend projection cells", () => {
    const experiment = orgExperiment({
      status: EXPERIMENT_STATUS_COMPLETED,
      measurement_status: "measured",
      outcome_comparison: {
        comparison_version: "1.4.0",
        measurement_status: "measured",
        window: { start: "2026-09-01T00:00:00Z", end: "2026-10-01T00:00:00Z", days: 30 },
        metrics: [
          {
            metric: "estimated_net_savings_usd",
            baseline_value: 1200,
            target_value: 2400,
            observed_value: 1900,
            change_from_baseline: 700,
            variance_from_target: -500,
            projected_value: 2600,
            variance_from_projection: -700,
            unit: "usd",
            direction_vs_baseline: "increased",
            direction_vs_target: "decreased",
            direction_vs_projection: "decreased",
            measurement_status: "measured",
            warning: null,
          },
        ],
        warnings: [],
        limitations: [EXPERIMENT_CAUSALITY_DISCLAIMER],
      },
    });

    const summary = buildExperimentExecutiveSummary(experiment);
    assert.ok(summary.valueRows !== null);
    assert.equal(summary.valueRows.length, 1);
    assert.equal(summary.valueRows[0].projected, "$2,600.00");
    assert.equal(summary.valueRows[0].observed, "$1,900.00");
    assert.equal(summary.valueRows[0].variance, "-$700");
  });

  it("returns the persisted limitation disclosure verbatim", () => {
    const experiment = orgExperiment({
      status: EXPERIMENT_STATUS_COMPLETED,
      measurement_status: "measured",
      outcome_comparison: {
        comparison_version: "1.4.0",
        measurement_status: "measured",
        window: null,
        metrics: [],
        warnings: [],
        limitations: ["Model limitation one.", "Model limitation two."],
      },
    });
    const summary = buildExperimentExecutiveSummary(experiment);
    assert.deepEqual(summary.limitations, [
      "Model limitation one.",
      "Model limitation two.",
    ]);
  });

  it("an unmeasured experiment yields an empty comparison", () => {
    const summary = buildExperimentExecutiveSummary(orgExperiment());
    assert.deepEqual(summary.comparisonRows, []);
    assert.equal(summary.narrative.length, 0);
    assert.deepEqual(summary.limitations, [EXPERIMENT_CAUSALITY_DISCLAIMER]);
  });
});

describe("api client", () => {
  it("list GETs the experiment collection path", async () => {
    let requestedUrl = "";
    const fetcher = (async (input: RequestInfo | URL) => {
      requestedUrl = String(input);
      return jsonResponse([]);
    }) as typeof fetch;

    const list = await fetchExperimentList(fetcher);
    assert.equal(requestedUrl, "/api/backend/service-operations/experiments");
    assert.deepEqual(list, []);
  });

  it("detail GETs the exact experiment id", async () => {
    let requestedUrl = "";
    const fetcher = (async (input: RequestInfo | URL) => {
      requestedUrl = String(input);
      return jsonResponse(orgExperiment());
    }) as typeof fetch;

    await fetchExperiment(7, fetcher);
    assert.equal(
      requestedUrl,
      "/api/backend/service-operations/experiments/7",
    );
  });

  it("lifecycle actions POST to the state-machine endpoint", async () => {
    const methodLog: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ) => {
      methodLog.push(init?.method ?? "?");
      return jsonResponse(orgExperiment({ status: EXPERIMENT_STATUS_READY }));
    }) as typeof fetch;

    await createExperiment({} as ServiceTransformationExperimentCreate, fetcher);
    assert.equal(methodLog[methodLog.length - 1], "POST");

    const captured = await runExperimentAction(3, "capture-baseline", fetcher);
    assert.equal(captured.status, EXPERIMENT_STATUS_READY);
  });

  it("capture-baseline targets the capture-baseline path", async () => {
    let requestedUrl = "";
    const fetcher = (async (input: RequestInfo | URL) => {
      requestedUrl = String(input);
      return jsonResponse(
        orgExperiment({ status: EXPERIMENT_STATUS_READY }),
      );
    }) as typeof fetch;

    await runExperimentAction(9, "capture-baseline", fetcher);
    assert.equal(
      requestedUrl,
      `/api/backend${EXPERIMENT_PATH}/9/capture-baseline`,
    );
  });

  it("409 transition errors surface the backend detail", async () => {
    const fetcher = stubFetch(
      jsonResponse(
        { detail: "An archived experiment is read-only." },
        { status: 409, ok: false },
      ),
    );
    await assert.rejects(
      () => runExperimentAction(5, "archive", fetcher),
      (error: unknown) => {
        assert.ok(error instanceof ExperimentRequestError);
        assert.equal(error.status, 409);
        assert.equal(error.detail, "An archived experiment is read-only.");
        return true;
      },
    );
  });

  it("client error carries the generic label when detail is missing", async () => {
    const fetcher = stubFetch(
      jsonResponse({}, { status: 500, ok: false }),
    );
    await assert.rejects(
      () => fetchExperimentList(fetcher),
      (error: unknown) => {
        assert.ok(error instanceof ExperimentRequestError);
        assert.equal(error.status, 500);
        assert.equal(error.detail, null);
        assert.match(error.message, /Could not load transformation experiments/);
        return true;
      },
    );
  });

  it("service queues fetch from the existing endpoint", async () => {
    let requestedUrl = "";
    const fetcher = (async (input: RequestInfo | URL) => {
      requestedUrl = String(input);
      return jsonResponse([
        { id: 1, key: "billing", name: "Billing", active: true, is_default: false, sla_policy_id: null },
      ]);
    }) as typeof fetch;

    const queues = await fetchServiceQueues(fetcher);
    assert.equal(requestedUrl, "/api/backend/service-queues");
    assert.equal(queues[0].key, "billing");
  });
});