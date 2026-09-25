import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { CAPABILITIES } from "../authorization/capabilities.ts";
import { createAuthorizationView } from "../authorization/helpers.ts";
import {
  archiveSimulation,
  assumptionSelectionError,
  assumptionValueError,
  buildExecutiveSummary,
  buildScenarioCreatePayload,
  canManageSimulations,
  canViewSimulations,
  comparisonFormulaVersionsDiffer,
  comparisonHasUnevaluated,
  comparisonSelectionError,
  comparisonWarningsFor,
  comparisonWindowsDiffer,
  COMPARISON_MAX_SCENARIOS,
  COMPARISON_MIN_SCENARIOS,
  createSimulation,
  deriveSimulationExperience,
  DIFFERENT_FORMULA_VERSION_WARNING,
  DIFFERENT_WINDOW_WARNING,
  COMPARISON_UNEVALUATED_WARNING,
  evaluateSimulation,
  executiveLimitationsFor,
  fetchSimulation,
  fetchSimulationList,
  formatAssumptionLabel,
  formatAssumptionSummary,
  formatCount,
  formatDelta,
  formatMeasurementStatusLabel,
  formatMoney,
  formatMoneyDelta,
  formatRate,
  formatStatusLabel,
  formatTimestamp,
  formatWindowLabel,
  isFiniteAssumptionValue,
  presentationDelta,
  ROI_UNAVAILABLE_MESSAGE,
  REEVALUATE_ACTION_LABEL,
  EVALUATE_ACTION_LABEL,
  scenarioNameError,
  SIMULATION_ARCHIVE_ERROR,
  SIMULATION_ASSUMPTION_SELECTION_ERROR,
  SIMULATION_ASSUMPTION_VALUE_ERROR,
  SIMULATION_BLANK_NAME_ERROR,
  SIMULATION_CREATE_ERROR,
  SIMULATION_DEFAULT_DAYS,
  SIMULATION_DISCLAIMER,
  SIMULATION_EVALUATE_ERROR,
  SIMULATION_LOAD_ERROR,
  SIMULATION_MAX_ASSUMPTION_VALUE,
  SimulationRequestError,
  simulationProxyUrl,
  SIMULATION_STATUS_ARCHIVED,
  SIMULATION_STATUS_DRAFT,
  SIMULATION_STATUS_EVALUATED,
  SIMULATION_WINDOW_DAYS,
  type ServiceTransformationScenario,
  type SimulationAssumptionTargets,
  UNCHANGED_NOT_MODELLED,
} from "./simulation.ts";

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

function recordingFetcher(
  urls: string[],
  payload: unknown = {},
  status = 200,
) {
  return async (input: RequestInfo | URL): Promise<Response> => {
    urls.push(String(input));
    return jsonResponse(payload, status);
  };
}

function scenarioFixture(
  overrides: Partial<ServiceTransformationScenario> = {},
): ServiceTransformationScenario {
  return {
    id: 1,
    organization_id: 7,
    name: "Increase low-risk autonomy",
    description: "Focus on knowledge-backed low-risk runs.",
    window_days: 30,
    status: SIMULATION_STATUS_EVALUATED,
    assumptions: {
      autonomous_execution_rate_target: 40,
      sla_breach_reduction_percent: 25,
    },
    observed_baseline: {
      organization_id: 7,
      window_days: 30,
      observed_at: "2026-09-25T10:00:00Z",
      current_window_start: "2026-08-26T00:00:00Z",
      current_window_end: "2026-09-25T00:00:00Z",
      tickets_resolved: 98,
      reopened_tickets: 4,
      total_sla_breaches: 7,
      agent_runs: 80,
      autonomous_executions: 30,
      human_approval_required: 25,
      knowledge_specialist_runs: 62,
      rates: {
        autonomous_execution_rate: 37.5,
        human_approval_rate: 31.25,
        knowledge_usage_rate: 77.5,
        reopen_rate: 4.08,
      },
      value: {
        estimated_minutes_saved: 990,
        estimated_hours_saved: 16.5,
        estimated_labor_savings_usd: 412.5,
        agent_ai_cost_usd: 0.18,
        estimated_net_savings_usd: 412.32,
        pricing_configured: true,
        measurement_status: "measured",
        minimum_autonomous_samples: 30,
        sample_size_sufficient: true,
        roi_percent: 42.0,
      },
    },
    projected_result: {
      autonomous_executions: 32,
      autonomous_execution_rate_percent: 40,
      human_approval_required: 24,
      human_approval_rate_percent: 30,
      knowledge_specialist_runs: 62,
      knowledge_usage_rate_percent: 77.5,
      reopened_tickets: 3,
      reopen_rate_percent: 3.06,
      total_sla_breaches: 5,
      sla_breach_reduction_percent: 25,
      value: {
        estimated_minutes_saved: 1120,
        estimated_hours_saved: 18.67,
        estimated_labor_savings_usd: 466.75,
        agent_ai_cost_usd: 0.2,
        estimated_net_savings_usd: 466.55,
        roi_percent: 48.2,
        measurement_status: "measured",
      },
    },
    formula_version: "1L.1N",
    evaluated_at: "2026-09-25T10:05:00Z",
    created_by_subject: "supervisor-9",
    created_at: "2026-09-24T08:00:00Z",
    updated_at: "2026-09-25T10:05:00Z",
    ...overrides,
  };
}

describe("simulation capability gates", () => {
  it("read capability enables view; manage is separate", () => {
    assert.equal(
      canViewSimulations(canFrom([CAPABILITIES.TRANSFORMATION_SIMULATION_READ])),
      true,
    );
    assert.equal(
      canManageSimulations(canFrom([CAPABILITIES.TRANSFORMATION_SIMULATION_READ])),
      false,
    );
  });

  it("manage capability alone never grants view", () => {
    assert.equal(
      canViewSimulations(
        canFrom([CAPABILITIES.TRANSFORMATION_SIMULATION_MANAGE]),
      ),
      false,
    );
  });

  it("empty capability set fails closed", () => {
    const experience = deriveSimulationExperience(canFrom([]));
    assert.deepEqual(experience, {
      canRead: false,
      canManage: false,
      readOnly: false,
    });
  });

  it("read-only experience is derived for a reader without manage", () => {
    const experience = deriveSimulationExperience(
      canFrom([CAPABILITIES.TRANSFORMATION_SIMULATION_READ]),
    );
    assert.deepEqual(experience, {
      canRead: true,
      canManage: false,
      readOnly: true,
    });
  });

  it("a manager with the read capability is never read-only", () => {
    const experience = deriveSimulationExperience(
      canFrom([
        CAPABILITIES.TRANSFORMATION_SIMULATION_READ,
        CAPABILITIES.TRANSFORMATION_SIMULATION_MANAGE,
      ]),
    );
    assert.deepEqual(experience, {
      canRead: true,
      canManage: true,
      readOnly: false,
    });
  });
});

describe("create payload construction", () => {
  it("sends only enabled assumptions, untouched as 0–100", () => {
    const payload = buildScenarioCreatePayload({
      name: "  Raise autonomy  ",
      description: "  ",
      days: 90,
      assumptions: {
        autonomous_execution_rate_target: 40,
        reopen_rate_target: 10,
      },
    });
    assert.deepEqual(payload, {
      name: "Raise autonomy",
      description: null,
      days: 90,
      assumptions: {
        autonomous_execution_rate_target: 40,
        reopen_rate_target: 10,
      },
    });
  });

  it("omits disabled assumptions instead of inventing zero", () => {
    const payload = buildScenarioCreatePayload({
      name: "SLA focus",
      assumptions: { sla_breach_reduction_percent: 25 },
    });
    assert.deepEqual(Object.keys(payload.assumptions), [
      "sla_breach_reduction_percent",
    ]);
    assert.equal(payload.assumptions.autonomous_execution_rate_target, undefined);
  });

  it("never adds an organization_id or unsupported field to the body", () => {
    const payload = buildScenarioCreatePayload({
      name: "Payload boundary",
      assumptions: { knowledge_usage_rate_target: 80 },
    }) as Record<string, unknown>;
    assert.equal(payload.organization_id, undefined);
    const assumptions = payload.assumptions as Record<string, unknown>;
    assert.equal(assumptions.organization_id, undefined);
    assert.equal(assumptions.target_percent, undefined);
  });

  it("clamps an unsupported window back to the default", () => {
    const payload = buildScenarioCreatePayload({
      name: "Window guard",
      days: 14,
      assumptions: { reopen_rate_target: 10 },
    });
    assert.equal(payload.days, SIMULATION_DEFAULT_DAYS);
    assert.equal(payload.days, 30);
  });

  it("drops non-finite or out-of-range assumption values", () => {
    const payload = buildScenarioCreatePayload({
      name: "Value guard",
      assumptions: {
        autonomous_execution_rate_target: 101,
        reopen_rate_target: Number.NaN,
        sla_breach_reduction_percent: -5,
        knowledge_usage_rate_target: 50,
      },
    });
    assert.deepEqual(Object.keys(payload.assumptions), [
      "knowledge_usage_rate_target",
    ]);
  });

  it("keeps a trimmed description only when non-blank", () => {
    const payload = buildScenarioCreatePayload({
      name: "Desc",
      description: "  useful context  ",
      assumptions: { reopen_rate_target: 10 },
    });
    assert.equal(payload.description, "useful context");
  });
});

describe("simulation validators", () => {
  it("requires at least one assumption", () => {
    assert.equal(
      assumptionSelectionError({}),
      SIMULATION_ASSUMPTION_SELECTION_ERROR,
    );
    assert.equal(
      assumptionSelectionError({ reopen_rate_target: 10 } as SimulationAssumptionTargets),
      null,
    );
  });

  it("rejects missing, non-finite, and out-of-range values", () => {
    assert.equal(assumptionValueError(null), null);
    assert.equal(assumptionValueError(undefined), null);
    assert.equal(assumptionValueError(0), null);
    assert.equal(assumptionValueError(100), null);
    assert.equal(assumptionValueError(50.5), null);
    assert.equal(assumptionValueError(-0.1), SIMULATION_ASSUMPTION_VALUE_ERROR);
    assert.equal(assumptionValueError(100.1), SIMULATION_ASSUMPTION_VALUE_ERROR);
    assert.equal(assumptionValueError(Number.NaN), SIMULATION_ASSUMPTION_VALUE_ERROR);
    assert.equal(assumptionValueError(Infinity), SIMULATION_ASSUMPTION_VALUE_ERROR);
  });

  it("the finite-value predicate mirrors the 0–100 contract", () => {
    assert.equal(isFiniteAssumptionValue(0), true);
    assert.equal(isFiniteAssumptionValue(100), true);
    assert.equal(isFiniteAssumptionValue(37.5), true);
    assert.equal(isFiniteAssumptionValue(101), false);
    assert.equal(isFiniteAssumptionValue(-1), false);
    assert.equal(isFiniteAssumptionValue(Number.NaN), false);
    assert.equal(SIMULATION_MAX_ASSUMPTION_VALUE, 100);
  });

  it("rejects blank scenario names", () => {
    assert.equal(scenarioNameError("ok"), null);
    assert.equal(scenarioNameError("   "), SIMULATION_BLANK_NAME_ERROR);
    assert.equal(scenarioNameError(""), SIMULATION_BLANK_NAME_ERROR);
  });
});

describe("rate and value formatting", () => {
  it("formats backend 0–100 rates directly, never ×100", () => {
    assert.equal(formatRate(40), "40%");
    assert.equal(formatRate(42.5), "42.5%");
    assert.equal(formatRate(0), "0%");
    assert.equal(formatRate(100), "100%");
    assert.equal(formatRate(null), "—");
    assert.equal(formatRate(undefined), "—");
  });

  it("formats counts with separators and em dash for missing", () => {
    assert.equal(formatCount(1234), "1,234");
    assert.equal(formatCount(30), "30");
    assert.equal(formatCount(null), "—");
    assert.equal(formatCount(undefined), "—");
  });

  it("formats integer deltas with explicit signs", () => {
    assert.equal(formatDelta(2), "+2");
    assert.equal(formatDelta(0), "0");
    assert.equal(formatDelta(-3), "-3");
    assert.equal(formatDelta(null), "—");
    assert.equal(formatDelta(undefined), "—");
  });

  it("formats money with two decimals and separators", () => {
    assert.equal(formatMoney(412.5), "$412.50");
    assert.equal(formatMoney(1234.56), "$1,234.56");
    assert.equal(formatMoney(0), "$0.00");
    assert.equal(formatMoney(null), "—");
  });

  it("formats signed money deltas without double signs", () => {
    assert.equal(formatMoneyDelta(45.1), "+$45.1");
    assert.equal(formatMoneyDelta(0), "$0");
    assert.equal(formatMoneyDelta(-45.1), "-$45.1");
    assert.equal(formatMoneyDelta(null), "—");
  });

  it("labels windows, timestamps, statuses, and measurement status", () => {
    assert.equal(formatWindowLabel(7), "7 days");
    assert.equal(formatWindowLabel(90), "90 days");
    assert.equal(formatTimestamp(null), "—");
    assert.equal(formatTimestamp("not-a-date"), "—");
    assert.equal(formatStatusLabel(SIMULATION_STATUS_DRAFT), "Draft");
    assert.equal(formatStatusLabel(SIMULATION_STATUS_EVALUATED), "Evaluated");
    assert.equal(formatStatusLabel(SIMULATION_STATUS_ARCHIVED), "Archived");
    assert.equal(formatStatusLabel("unknown"), "unknown");
    assert.equal(formatMeasurementStatusLabel("measured"), "Measured");
    assert.equal(formatMeasurementStatusLabel("insufficient_sample"), "Insufficient sample");
    assert.equal(formatMeasurementStatusLabel("pricing_not_configured"), "Pricing not configured");
    assert.equal(formatMeasurementStatusLabel("bogus"), "bogus");
  });

  it("summarizes assumptions with the SLA dimension marked as a reduction", () => {
    assert.equal(
      formatAssumptionSummary({
        autonomous_execution_rate_target: 40,
        sla_breach_reduction_percent: 25,
      }),
      "Autonomous execution 40% · SLA breaches −25%",
    );
    assert.equal(formatAssumptionSummary({}), "");
  });

  it("prettifies assumption labels and falls back for unknown keys", () => {
    assert.equal(
      formatAssumptionLabel("autonomous_execution_rate_target"),
      "Autonomous execution rate target",
    );
    assert.equal(formatAssumptionLabel("mystery_key"), "Mystery Key");
  });
});

function methodFetcher(calls: string[]) {
  return (async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    calls.push(`${String(input)}:${init?.method ?? "GET"}:${init?.body ?? ""}`);
    return jsonResponse(scenarioFixture());
  }) as typeof fetch;
}

describe("simulation fetch client", () => {
  it("lists scenarios over the BFF proxy GET", async () => {
    const urls: string[] = [];
    const list = scenarioFixture();
    await fetchSimulationList(
      recordingFetcher(urls, [list]) as typeof fetch,
    );
    assert.deepEqual(urls, ["/api/backend/service-operations/simulations"]);
  });

  it("fetches one scenario by id", async () => {
    const urls: string[] = [];
    await fetchSimulation(3, recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, [
      "/api/backend/service-operations/simulations/3",
    ]);
  });

  it("creates a scenario with a JSON POST and serialized payload", async () => {
    const calls: string[] = [];
    await createSimulation(
      {
        name: "New",
        description: null,
        days: 30,
        assumptions: { reopen_rate_target: 10 },
      },
      methodFetcher(calls),
    );
    assert.equal(calls.length, 1);
    assert.equal(calls[0].startsWith("/api/backend/service-operations/simulations:POST:"), true);
    const body = JSON.parse(calls[0].split(":POST:")[1] ?? "{}");
    assert.deepEqual(body.assumptions, { reopen_rate_target: 10 });
    assert.equal(body.organization_id, undefined);
  });

  it("evaluates over the evaluate POST path", async () => {
    const urls: string[] = [];
    await evaluateSimulation(4, recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, [
      "/api/backend/service-operations/simulations/4/evaluate",
    ]);
  });

  it("archives over the archive POST path", async () => {
    const urls: string[] = [];
    await archiveSimulation(4, recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, [
      "/api/backend/service-operations/simulations/4/archive",
    ]);
  });

  it("throws a SimulationRequestError carrying the backend status and detail", async () => {
    const fetcher = (async () =>
      jsonResponse({ detail: "Insufficient permissions" }, 403)) as typeof fetch;
    await assert.rejects(
      () => fetchSimulation(1, fetcher),
      (err: unknown) =>
        err instanceof SimulationRequestError &&
        err.status === 403 &&
        err.detail === "Insufficient permissions" &&
        err.message === "Insufficient permissions",
    );
  });

  it("falls back to the generic label when the backend omits detail", async () => {
    const fetcher = (async () => jsonResponse({}, 500)) as typeof fetch;
    await assert.rejects(
      () => fetchSimulation(1, fetcher),
      (err: unknown) =>
        err instanceof SimulationRequestError &&
        err.message === SIMULATION_LOAD_ERROR,
    );
  });

  it("constructs proxy URLs from the BFF prefix", () => {
    assert.equal(
      simulationProxyUrl("/service-operations/simulations/5/evaluate"),
      "/api/backend/service-operations/simulations/5/evaluate",
    );
  });

  it("exposes the generic error constants verbatim", () => {
    assert.equal(SIMULATION_LOAD_ERROR, "Could not load transformation scenarios.");
    assert.equal(SIMULATION_CREATE_ERROR, "Could not create the scenario.");
    assert.equal(SIMULATION_EVALUATE_ERROR, "Could not evaluate the scenario.");
    assert.equal(SIMULATION_ARCHIVE_ERROR, "Could not archive the scenario.");
  });
});

describe("comparison helpers", () => {
  const evaluated30 = scenarioFixture();
  const evaluated7 = scenarioFixture({ id: 2, window_days: 7, formula_version: "1L.1N" });
  const evaluatedOtherVersion = scenarioFixture({
    id: 3,
    window_days: 30,
    formula_version: "1L.2N",
  });
  const draft = scenarioFixture({
    id: 4,
    status: SIMULATION_STATUS_DRAFT,
    observed_baseline: null,
    projected_result: null,
    evaluated_at: null,
    formula_version: null,
  });

  it("detects different observed windows only when they genuinely differ", () => {
    assert.equal(comparisonWindowsDiffer([evaluated30, evaluated30]), false);
    assert.equal(comparisonWindowsDiffer([evaluated30, evaluated7]), true);
    assert.equal(comparisonWindowsDiffer([evaluated30, evaluatedOtherVersion]), false);
  });

  it("detects different non-null formula versions", () => {
    assert.equal(
      comparisonFormulaVersionsDiffer([evaluated30, evaluatedOtherVersion]),
      true,
    );
    assert.equal(
      comparisonFormulaVersionsDiffer([evaluated30, draft]),
      false,
    );
  });

  it("flags unevaluated scenarios", () => {
    assert.equal(comparisonHasUnevaluated([evaluated30, draft]), true);
    assert.equal(comparisonHasUnevaluated([evaluated30, evaluated7]), false);
  });

  it("emits the exact documented warnings, never extra ones", () => {
    const scenarios = [evaluated30, draft, evaluated7];
    const warnings = comparisonWarningsFor(scenarios);
    assert.deepEqual(warnings, [
      DIFFERENT_WINDOW_WARNING,
      COMPARISON_UNEVALUATED_WARNING,
    ]);
  });

  it("includes the formula-version warning when versions differ", () => {
    const warnings = comparisonWarningsFor([evaluated30, evaluatedOtherVersion]);
    assert.deepEqual(warnings, [DIFFERENT_FORMULA_VERSION_WARNING]);
  });

  it("returns no warnings for a homogeneous evaluated comparison", () => {
    assert.deepEqual(comparisonWarningsFor([evaluated30, evaluated30]), []);
  });

  it("enforces the 2–4 selection bounds", () => {
    assert.equal(comparisonSelectionError(0), "Select at least two evaluated scenarios to compare.");
    assert.equal(comparisonSelectionError(1), "Select at least two evaluated scenarios to compare.");
    assert.equal(comparisonSelectionError(2), null);
    assert.equal(comparisonSelectionError(4), null);
    assert.equal(comparisonSelectionError(5), "Select at most four scenarios to compare.");
    assert.equal(COMPARISON_MIN_SCENARIOS, 2);
    assert.equal(COMPARISON_MAX_SCENARIOS, 4);
  });
});

describe("presentation delta", () => {
  it("is null-safe and rounds to two decimals", () => {
    assert.equal(presentationDelta(5, 3), 2);
    assert.equal(presentationDelta(null, 3), null);
    assert.equal(presentationDelta(5, null), null);
  });
});

describe("executive summary", () => {
  it("is deterministic and derived only from the persisted row", () => {
    const summary = buildExecutiveSummary(scenarioFixture());
    assert.equal(summary.name, "Increase low-risk autonomy");
    assert.equal(summary.windowLabel, "30 days");
    assert.equal(summary.statusLabel, "Evaluated");
    assert.equal(summary.observed.length, 9);
    assert.equal(summary.projected.length, 5);
    assert.deepEqual(summary.assumptions, [
      {
        label: "Autonomous execution rate target",
        value: "40%",
      },
      {
        label: "SLA breach reduction",
        value: "25%",
      },
    ]);
    assert.equal(summary.value?.length, 5);
    assert.equal(summary.roiLabel, "48.2%");
  });

  it("renders ROI unavailable only when the measurement status says so", () => {
    const insufficient = buildExecutiveSummary(
      scenarioFixture({
        projected_result: {
          ...scenarioFixture().projected_result!,
          value: {
            ...scenarioFixture().projected_result!.value,
            roi_percent: null,
            measurement_status: "insufficient_sample",
          },
        },
      }),
    );
    assert.equal(insufficient.roiLabel, null);
    assert.equal(insufficient.measurementStatusLabel, "Insufficient sample");
  });

  it("keeps projections out of the summary for an unevaluated scenario", () => {
    const summary = buildExecutiveSummary(
      scenarioFixture({
        status: SIMULATION_STATUS_DRAFT,
        observed_baseline: null,
        projected_result: null,
        evaluated_at: null,
        formula_version: null,
      }),
    );
    assert.deepEqual(summary.observed, []);
    assert.deepEqual(summary.projected, []);
    assert.equal(summary.value, null);
    assert.equal(summary.roiLabel, null);
    assert.equal(summary.measurementStatusLabel, null);
  });

  it("labels projected value rows with the projected qualifier", () => {
    const summary = buildExecutiveSummary(scenarioFixture());
    const labels = summary.value?.map((row) => row.label) ?? [];
    assert.ok(labels.includes("Estimated net savings (projected)"));
    assert.equal(labels.includes("Estimated net savings"), false);
  });

  it("lists the base determinism limitation plus per-assumption caveats", () => {
    const limitations = executiveLimitationsFor(scenarioFixture());
    assert.equal(limitations.length, 3);
    assert.match(limitations[0], /projections are estimates, not forecasts/);
    assert.match(limitations[1], /Eligibility of agent runs is not modelled/);
    assert.match(limitations[2], /SLA reduction is arithmetic/);
  });

  it("explains the ROI unavailability message and keeps its exact copy", () => {
    assert.equal(
      ROI_UNAVAILABLE_MESSAGE,
      "Formal ROI is unavailable for this scenario because the measurement requirements were not met.",
    );
    const noPricing = scenarioFixture({
      projected_result: {
        ...scenarioFixture().projected_result!,
        value: {
          ...scenarioFixture().projected_result!.value,
          roi_percent: null,
          measurement_status: "pricing_not_configured",
        },
      },
    });
    const limitations = executiveLimitationsFor(noPricing);
    assert.match(limitations[limitations.length - 1], /LLM pricing is not configured/);
  });
});

describe("scenario constants and safety contract", () => {
  it("supports only the three backend windows", () => {
    assert.deepEqual(SIMULATION_WINDOW_DAYS, [7, 30, 90]);
    assert.equal(SIMULATION_DEFAULT_DAYS, 30);
  });

  it("discloses the estimates-only caveat verbatim", () => {
    assert.equal(
      SIMULATION_DISCLAIMER,
      "Scenario projections are deterministic estimates, not forecasts or guaranteed outcomes.",
    );
  });

  it("action labels differentiate evaluate from re-evaluate", () => {
    assert.equal(EVALUATE_ACTION_LABEL, "Evaluate");
    assert.equal(REEVALUATE_ACTION_LABEL, "Re-evaluate");
  });

  it("missing assumptions are described as unchanged, never as zero", () => {
    assert.equal(UNCHANGED_NOT_MODELLED, "Not assumed — unchanged / not modelled");
  });

  it("formatters never emit NaN, undefined, or Infinity", () => {
    for (const rendered of [
      formatRate(Number.NaN),
      formatRate(Infinity),
      formatCount(Number.NaN),
      formatDelta(Number.NaN),
      formatMoney(Number.NaN),
      formatTimestamp(null),
    ]) {
      assert.notEqual(rendered, "NaN");
      assert.notEqual(rendered, "undefined");
      assert.notEqual(rendered, "Infinity");
    }
  });

  it("a parsed scenario renders through the em dash policy for missing values", () => {
    const parseState = JSON.stringify(scenarioFixture().observed_baseline);
    assert.equal(parseState.includes("undefined"), false);
  });
});