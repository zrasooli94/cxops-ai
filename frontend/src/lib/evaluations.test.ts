import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { CAPABILITIES } from "./authorization/capabilities.ts";
import { createAuthorizationView } from "./authorization/helpers.ts";
import {
  AGENT_INTENT_OPTIONS,
  SPECIALIST_PATH_PRESETS,
  EVALUATION_DECISION_APPROVED_MESSAGE,
  EVALUATION_DECISION_BLOCKED_MESSAGE,
  EVALUATION_DECISION_ERROR,
  EVALUATION_DECISION_NOTE_MAX,
  EVALUATION_DECISION_REJECTED_MESSAGE,
  EVALUATION_DECISION_SAVING_LABEL,
  EVALUATION_DECISIONS_LOAD_ERROR,
  EVALUATION_PROMOTE_ERROR,
  EVALUATION_PROMOTE_SUCCESS,
  EVALUATION_PROMOTING_LABEL,
  EVALUATION_QUEUED_MESSAGE,
  EVALUATION_START_ERROR,
  baselineSelectLabel,
  canManageEvaluations,
  canPromoteRunToBaseline,
  canShowReleaseDecisionActions,
  canViewEvaluations,
  caseDisplayRow,
  compareRunToBaseline,
  comparisonSummary,
  createRunBaseline,
  decisionLabel,
  dimensionsForCase,
  directionLabel,
  fetchEvaluationBaselines,
  fetchEvaluationRunCases,
  fetchEvaluationRun,
  fetchEvaluationRuns,
  fetchReleaseDecisions,
  formatLatency,
  formatPassRate,
  formatPassRateDelta,
  formatTimestamp,
  identityRows,
  isKnownEvaluationTarget,
  metricComparisonRows,
  promoteRunToBaseline,
  queueAgentEvaluation,
  queueRagEvaluation,
  recordReleaseDecision,
  ReleaseDecisionBlockedError,
  releaseDecisionRow,
  reviewReleaseDecision,
  runDisplayRow,
  runStatusLabel,
  specialistPathForPreset,
  startAgentEvaluation,
  startRagEvaluation,
  targetTypeLabel,
  type EvaluationBaseline,
  type EvaluationCaseResult,
  type EvaluationReleaseDecisionRecord,
  type EvaluationRun,
  type EvaluationRunComparison,
} from "./evaluations.ts";

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
    return jsonResponse({ items: [], total: 0 });
  };
}

const RUN: EvaluationRun = {
  id: 1,
  run_id: "run-abc",
  organization_id: 1,
  target_type: "rag",
  status: "succeeded",
  model: "gpt-4o",
  embedding_model: null,
  agent_decision_version: "v2.1.0",
  tool_policy_version: 3,
  corpus_revision: null,
  trigger_source: "manual",
  requested_by_subject: null,
  pass_rate: 0.85,
  metrics: {},
  error: null,
  created_at: "2026-09-24T10:00:00Z",
  started_at: "2026-09-24T10:00:05Z",
  completed_at: "2026-09-24T10:02:00Z",
};

const BASELINE: EvaluationBaseline = {
  id: 7,
  organization_id: 1,
  target_type: "rag",
  version: "v2",
  model: "gpt-4o",
  embedding_model: null,
  agent_decision_version: "v2.1.0",
  tool_policy_version: 3,
  corpus_revision: null,
  pass_rate: 0.85,
  metrics: { retrieval_accuracy: 0.8 },
  cases_count: 5,
  created_by_subject: "auth:11",
  promoted: true,
  created_at: "2026-09-24T09:00:00Z",
};

const COMPARISON: EvaluationRunComparison = {
  target_type: "rag",
  candidate_pass_rate: 0.9,
  baseline_pass_rate: 0.85,
  pass_rate_delta: 0.05,
  metrics: [
    {
      metric: "retrieval_accuracy",
      baseline: 0.8,
      candidate: 0.92,
      delta: 0.12,
      direction: "improved",
    },
    {
      metric: "answer_correctness",
      baseline: 0.9,
      candidate: 0.85,
      delta: -0.05,
      direction: "regressed",
    },
    {
      metric: "grounding_accuracy",
      baseline: 0.95,
      candidate: 0.95,
      delta: 0,
      direction: "same",
    },
  ],
  baseline: {
    model: "gpt-4o",
    embedding_model: null,
    agent_decision_version: "v2.1.0",
    tool_policy_version: 3,
    corpus_revision: { rev: "abc" },
  },
  candidate: {
    model: "gpt-4o-mini",
    embedding_model: "text-embedding-3",
    agent_decision_version: null,
    tool_policy_version: null,
    corpus_revision: null,
  },
};

const SUCCEEDED_RUN: EvaluationRun = { ...RUN, status: "succeeded" };

describe("evaluation capability gate", () => {
  it("evaluation.read enables the read-only page", () => {
    assert.equal(canViewEvaluations(canFrom([CAPABILITIES.EVALUATION_READ])), true);
  });

  it("evaluation.manage alone never unlocks the read-only page", () => {
    assert.equal(canViewEvaluations(canFrom([CAPABILITIES.EVALUATION_MANAGE])), false);
  });

  it("without evaluation.read the page cannot view or fetch evaluations", () => {
    const viewer = canFrom([CAPABILITIES.TICKET_READ, CAPABILITIES.CUSTOMER_READ]);
    assert.equal(canViewEvaluations(viewer), false);
  });

  it("an unrelated capability never enables evaluations", () => {
    assert.equal(canViewEvaluations(canFrom(["evaluation.superuser"])), false);
  });

  it("an empty capability set fails closed", () => {
    assert.equal(canViewEvaluations(canFrom([])), false);
  });
});

describe("evaluation GET helpers", () => {
  it("fetchEvaluationRuns requests the runs list with limit=50&offset=0 by default", async () => {
    const urls: string[] = [];
    await fetchEvaluationRuns({}, recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, [
      "/api/backend/evaluations/runs?limit=50&offset=0",
    ]);
  });

  it("fetchEvaluationRuns forwards a custom limit and offset", async () => {
    const urls: string[] = [];
    await fetchEvaluationRuns({ limit: 200, offset: 100 }, recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, [
      "/api/backend/evaluations/runs?limit=200&offset=100",
    ]);
  });

  it("fetchEvaluationRun requests the run detail endpoint", async () => {
    const urls: string[] = [];
    await fetchEvaluationRun("run-abc", recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, ["/api/backend/evaluations/runs/run-abc"]);
  });

  it("fetchEvaluationRun encodes a report string run id", async () => {
    const urls: string[] = [];
    await fetchEvaluationRun("a/b c", recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, ["/api/backend/evaluations/runs/a%2Fb%20c"]);
  });

  it("fetchEvaluationRunCases requests the run cases endpoint", async () => {
    const urls: string[] = [];
    await fetchEvaluationRunCases("run-abc", recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, ["/api/backend/evaluations/runs/run-abc/cases"]);
  });

  it("fetchEvaluationRun parses a run list response", async () => {
    const urls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      urls.push(String(input));
      return jsonResponse({
        items: [RUN],
        total: 1,
        limit: 50,
        offset: 0,
      });
    };
    const response = await fetchEvaluationRuns({}, fetcher);
    assert.equal(response.total, 1);
    assert.equal(response.items[0].run_id, "run-abc");
  });

  it("throws a friendly error when the backend is not ok", async () => {
    const fetcher: typeof fetch = async () => jsonResponse({}, 503);
    await assert.rejects(
      () => fetchEvaluationRuns({}, fetcher),
      /Could not load evaluation data/,
    );
  });
});

describe("run list rendering", () => {
  it("renders target, status, pass rate, and model from a run", () => {
    const row = runDisplayRow(RUN);
    assert.equal(row.target, "RAG");
    assert.equal(row.statusLabel, "Succeeded");
    assert.equal(row.passRate, "85%");
    assert.equal(row.model, "gpt-4o");
  });

  it("renders created and completed labels from a run", () => {
    const row = runDisplayRow(RUN);
    assert.equal(row.createdLabel === "—", false);
    assert.equal(row.completedLabel === "—", false);
  });

  it("renders em dash for a run that has not completed yet", () => {
    const row = runDisplayRow({ ...RUN, completed_at: null });
    assert.equal(row.completedLabel, "—");
  });

  it("maps the four backend statuses to clear labels", () => {
    assert.equal(runStatusLabel("queued"), "Queued");
    assert.equal(runStatusLabel("running"), "Running");
    assert.equal(runStatusLabel("succeeded"), "Succeeded");
    assert.equal(runStatusLabel("failed"), "Failed");
  });

  it("falls back to the raw status string for an unknown status", () => {
    assert.equal(runStatusLabel("weird"), "weird");
  });

  it("renders target labels for known and unknown targets", () => {
    assert.equal(targetTypeLabel("rag"), "RAG");
    assert.equal(targetTypeLabel("agent"), "Agent");
    assert.equal(targetTypeLabel("repeatability"), "Repeatability");
    assert.equal(targetTypeLabel("latency"), "Latency");
    assert.equal(targetTypeLabel("mystery"), "mystery");
  });
});

describe("pass rate formatting", () => {
  it("renders a decimal pass rate as a percentage", () => {
    assert.equal(formatPassRate(0.85), "85%");
    assert.equal(formatPassRate(1), "100%");
    assert.equal(formatPassRate(0), "0%");
  });

  it("renders em dash for a null or missing pass rate", () => {
    assert.equal(formatPassRate(null), "—");
    assert.equal(formatPassRate(undefined), "—");
  });

  it("does not invent a score for a missing pass rate", () => {
    assert.equal(runDisplayRow({ ...RUN, pass_rate: null }).passRate, "—");
  });
});

describe("timestamp formatting", () => {
  it("renders em dash for null or invalid timestamps", () => {
    assert.equal(formatTimestamp(null), "—");
    assert.equal(formatTimestamp(undefined), "—");
    assert.equal(formatTimestamp("not-a-date"), "—");
  });

  it("renders a readable local date for a valid timestamp", () => {
    const label = formatTimestamp("2026-09-24T10:00:00Z");
    assert.equal(label.includes("Sep"), true);
  });
});

describe("case dimensions", () => {
  it("renders only RAG dimensions that exist in the response", () => {
    const dimensions = dimensionsForCase("rag", {
      retrieval_hit: true,
      answer_correct: false,
      grounding_correct: true,
    });
    assert.deepEqual(dimensions, [
      { label: "Retrieval", value: "true" },
      { label: "Answer", value: "false" },
      { label: "Grounding", value: "true" },
    ]);
  });

  it("does not guess missing RAG dimensions", () => {
    const dimensions = dimensionsForCase("rag", { retrieval_hit: true });
    assert.deepEqual(dimensions, [{ label: "Retrieval", value: "true" }]);
  });

  it("renders only Agent dimensions that exist in the response", () => {
    const dimensions = dimensionsForCase("agent", {
      action_pass: true,
      retrieval_pass: false,
      overall_pass: true,
      tool_pass: true,
    });
    assert.deepEqual(dimensions, [
      { label: "Action", value: "true" },
      { label: "Retrieval", value: "false" },
      { label: "Tool", value: "true" },
      { label: "Overall", value: "true" },
    ]);
  });

  it("renders the Intent dimension only when the case was intent-scored", () => {
    const withIntent = dimensionsForCase("agent", {
      action_pass: true,
      intent_pass: true,
      overall_pass: true,
    });
    assert.deepEqual(withIntent, [
      { label: "Action", value: "true" },
      { label: "Intent", value: "true" },
      { label: "Overall", value: "true" },
    ]);

    // Unscored cases never carry the dimension in the response.
    const withoutIntent = dimensionsForCase("agent", {
      action_pass: true,
      overall_pass: true,
    });
    assert.deepEqual(withoutIntent, [
      { label: "Action", value: "true" },
      { label: "Overall", value: "true" },
    ]);

    // A scored-and-failed intent still renders verbatim (backend dimensions).
    const failedIntent = dimensionsForCase("agent", {
      action_pass: true,
      intent_pass: false,
      overall_pass: false,
    });
    assert.deepEqual(failedIntent, [
      { label: "Action", value: "true" },
      { label: "Intent", value: "false" },
      { label: "Overall", value: "false" },
    ]);
  });

  it("does not invent an Intent dimension for old uncharted agent runs", () => {
    const dimensions = dimensionsForCase("agent", {
      action_pass: true,
      overall_pass: true,
    });
    assert.deepEqual(dimensions, [
      { label: "Action", value: "true" },
      { label: "Overall", value: "true" },
    ]);
  });

  it("renders the Specialist path dimension between Intent and Overall", () => {
    const dimensions = dimensionsForCase("agent", {
      action_pass: true,
      intent_pass: true,
      specialist_path_pass: true,
      overall_pass: true,
    });
    assert.deepEqual(dimensions, [
      { label: "Action", value: "true" },
      { label: "Intent", value: "true" },
      { label: "Specialist path", value: "true" },
      { label: "Overall", value: "true" },
    ]);

    // Unscored cases never carry the dimension in the response.
    const unscored = dimensionsForCase("agent", {
      action_pass: true,
      intent_pass: true,
      overall_pass: true,
    });
    assert.deepEqual(unscored, [
      { label: "Action", value: "true" },
      { label: "Intent", value: "true" },
      { label: "Overall", value: "true" },
    ]);
  });

  it("ignores sensitive-looking keys that are not part of the dimension contract", () => {
    const dimensions = dimensionsForCase("rag", {
      customer_email: "a@b.co",
      prompt: "system: you are...",
    });
    assert.deepEqual(dimensions, []);
  });
});

describe("safe rendering contract", () => {
  const CASE: EvaluationCaseResult = {
    id: 10,
    run_id: 1,
    organization_id: 1,
    case_id: "case-1",
    case_type: "rag",
    expected: {},
    actual: {},
    dimensions: { retrieval_hit: true, citation_valid: false },
    latency_ms: 420.6,
    total_tokens: 1200,
    estimated_cost_usd: 0.01,
    fingerprint: null,
    created_at: "2026-09-24T10:02:00Z",
  };

  it("case rows expose only safe fields", () => {
    const row = caseDisplayRow(CASE);
    assert.equal(row.caseId, "case-1");
    assert.equal(row.caseType, "rag");
    assert.equal(row.latencyLabel, "421ms");
  });

  it("does not render run input or raw prompt/email/phone fields", () => {
    const polluted = {
      ...RUN,
      // @ts-expect-error — a hostile payload smuggling sensitive keys past the API contract
      input: { subject: "How do I reset my password?", requester_email: "a@b.co" },
      raw_prompt: "system: you are...",
      customer_phone: "+1-555-0100",
    };
    const row = runDisplayRow(polluted);
    assert.deepEqual(Object.keys(row).sort(), [
      "completedLabel",
      "createdLabel",
      "model",
      "passRate",
      "statusLabel",
      "target",
    ]);
    assert.equal("input" in row, false);
    assert.equal("raw_prompt" in row, false);
    assert.equal("customer_phone" in row, false);
  });

  it("case display rows never carry the case input snapshot", () => {
    const polluted = {
      ...CASE,
      // @ts-expect-error — hostile payload smuggling case input past the contract
      input: { question: "What is my balance?", ticket_id: 99 },
    };
    const row = caseDisplayRow(polluted);
    assert.equal("input" in row, false);
    assert.equal(JSON.stringify(row).includes("What is my balance"), false);
  });

  it("latency renders em dash when absent", () => {
    assert.equal(formatLatency(null), "—");
    assert.equal(formatLatency(undefined), "—");
  });
});

describe("baseline fetch GET helper", () => {
  it("recognises only backend target-type literals", () => {
    assert.equal(isKnownEvaluationTarget("rag"), true);
    assert.equal(isKnownEvaluationTarget("agent"), true);
    assert.equal(isKnownEvaluationTarget("mystery"), false);
    assert.equal(isKnownEvaluationTarget(""), false);
  });

  it("requests baselines filtered by the run's target type", async () => {
    const urls: string[] = [];
    await fetchEvaluationBaselines(
      { targetType: "rag" },
      recordingFetcher(urls) as typeof fetch,
    );
    assert.deepEqual(urls, [
      "/api/backend/evaluations/baselines?target_type=rag&limit=50",
    ]);
  });

  it("requests all baselines when the run targets an unknown type", async () => {
    const urls: string[] = [];
    await fetchEvaluationBaselines(
      { targetType: "mystery" },
      recordingFetcher(urls) as typeof fetch,
    );
    assert.deepEqual(urls, ["/api/backend/evaluations/baselines?limit=50"]);
  });

  it("forwards a custom limit", async () => {
    const urls: string[] = [];
    await fetchEvaluationBaselines(
      { targetType: "agent", limit: 10 },
      recordingFetcher(urls) as typeof fetch,
    );
    assert.deepEqual(urls, [
      "/api/backend/evaluations/baselines?target_type=agent&limit=10",
    ]);
  });

  it("parses a baseline list response", async () => {
    const fetcher: typeof fetch = async () =>
      jsonResponse({ items: [BASELINE], total: 1 });
    const response = await fetchEvaluationBaselines({ targetType: "rag" }, fetcher);
    assert.equal(response.total, 1);
    assert.equal(response.items[0].id, 7);
    assert.equal(response.items[0].version, "v2");
  });

  it("throws a friendly error when the baseline list is not ok", async () => {
    const fetcher: typeof fetch = async () => jsonResponse({}, 403);
    await assert.rejects(
      () => fetchEvaluationBaselines({ targetType: "rag" }, fetcher),
      /Could not load baselines/,
    );
  });
});

describe("baseline selector label", () => {
  it("renders target, version, and pass rate", () => {
    assert.equal(baselineSelectLabel(BASELINE), "RAG · v2 · 85%");
  });

  it("renders em dash when the baseline has no pass rate", () => {
    assert.equal(
      baselineSelectLabel({ ...BASELINE, pass_rate: null }),
      "RAG · v2 · —",
    );
  });
});

describe("run-to-baseline compare GET helper", () => {
  it("requests the compare endpoint with the baseline id", async () => {
    const urls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      urls.push(String(input));
      return jsonResponse(COMPARISON);
    };
    const result = await compareRunToBaseline("run-abc", 7, fetcher);
    assert.deepEqual(urls, [
      "/api/backend/evaluations/runs/run-abc/compare/7",
    ]);
    assert.equal(result.baseline_pass_rate, 0.85);
  });

  it("encodes a raw run id in the compare URL", async () => {
    const urls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      urls.push(String(input));
      return jsonResponse(COMPARISON);
    };
    await compareRunToBaseline("a/b c", 7, fetcher);
    assert.deepEqual(urls, [
      "/api/backend/evaluations/runs/a%2Fb%20c/compare/7",
    ]);
  });

  it("throws a friendly error when the compare is not ok", async () => {
    const fetcher: typeof fetch = async () => jsonResponse({}, 400);
    await assert.rejects(
      () => compareRunToBaseline("run-abc", 7, fetcher),
      /Could not compare evaluation/,
    );
  });

  it("sends only a GET with no body", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${init?.method ?? "GET"}`);
      return jsonResponse(COMPARISON);
    }) as typeof fetch;
    await compareRunToBaseline("run-abc", 7, fetcher);
    assert.deepEqual(calls, [
      "/api/backend/evaluations/runs/run-abc/compare/7:GET",
    ]);
  });
});

describe("comparison summary rendering", () => {
  it("renders baseline, candidate, and signed difference", () => {
    const summary = comparisonSummary(COMPARISON);
    assert.equal(summary.baselinePassRate, "85%");
    assert.equal(summary.candidatePassRate, "90%");
    assert.equal(summary.deltaLabel, "+5%");
  });

  it("renders em dash for missing pass rates and delta", () => {
    const summary = comparisonSummary({
      ...COMPARISON,
      candidate_pass_rate: null,
      pass_rate_delta: null,
    });
    assert.equal(summary.candidatePassRate, "—");
    assert.equal(summary.deltaLabel, "—");
  });

  it("formats deltas with explicit signs", () => {
    assert.equal(formatPassRateDelta(0.1), "+10%");
    assert.equal(formatPassRateDelta(-0.05), "-5%");
    assert.equal(formatPassRateDelta(0), "0%");
  });
});

describe("metric comparison rows", () => {
  it("renders each backends metric with baseline, candidate, change, direction", () => {
    const rows = metricComparisonRows(COMPARISON.metrics);
    assert.equal(rows.length, 3);
    assert.deepEqual(rows[0], {
      metric: "retrieval_accuracy",
      baselineLabel: "0.8",
      candidateLabel: "0.92",
      deltaLabel: "+0.12",
      direction: "improved",
      directionLabel: "Improved",
    });
  });

  it("renders the three backend directions verbatim", () => {
    assert.equal(directionLabel("improved"), "Improved");
    assert.equal(directionLabel("regressed"), "Regressed");
    assert.equal(directionLabel("same"), "Same");
    assert.equal(directionLabel("mystery"), "mystery");
  });

  it("does not invent metrics when none were compared", () => {
    assert.deepEqual(metricComparisonRows([]), []);
  });

  it("renders the intent_accuracy metric generically when the backend compares it", () => {
    const rows = metricComparisonRows([
      {
        metric: "intent_accuracy",
        baseline: 0.8,
        candidate: 1,
        delta: 0.2,
        direction: "improved",
      },
    ]);
    assert.deepEqual(rows[0], {
      metric: "intent_accuracy",
      baselineLabel: "0.8",
      candidateLabel: "1",
      deltaLabel: "+0.2",
      direction: "improved",
      directionLabel: "Improved",
    });
  });

  it("never invents an intent_accuracy row when only the candidate scored it", () => {
    // The backend skips metrics absent on either side; None is never sent.
    const rows = metricComparisonRows([
      {
        metric: "auto_execute_safety_accuracy",
        baseline: 0.9,
        candidate: 1,
        delta: 0.1,
        direction: "improved",
      },
    ]);
    assert.equal(rows[0].metric, "auto_execute_safety_accuracy");
    assert.equal(rows.some((row) => row.metric === "intent_accuracy"), false);
  });
});

describe("version identity rendering", () => {
  it("renders only identity fields that are present", () => {
    const rows = identityRows(COMPARISON.baseline);
    assert.deepEqual(rows, [
      { label: "Model", value: "gpt-4o" },
      { label: "Agent decision version", value: "v2.1.0" },
      { label: "Tool policy version", value: "3" },
      { label: "Corpus revision", value: "present" },
    ]);
  });

  it("renders the corpus revision only as present, never its contents", () => {
    const rows = identityRows(COMPARISON.baseline);
    assert.equal(JSON.stringify(rows).includes("abc"), false);
  });

  it("renders nothing for a snapshot with no identity fields", () => {
    assert.deepEqual(
      identityRows({
        model: "",
        embedding_model: null,
        agent_decision_version: null,
        tool_policy_version: null,
        corpus_revision: null,
      }),
      [{ label: "Model", value: "" }],
    );
  });
});

describe("baseline comparison safe rendering contract", () => {
  it("never carries run input or sensitive payload keys into comparison rows", () => {
    const polluted = {
      ...COMPARISON,
      baseline: {
        ...COMPARISON.baseline,
        customer_email: "a@b.co",
        prompt: "system: you are...",
      },
      candidate: {
        ...COMPARISON.candidate,
        raw_input: { subject: "How do I reset my password?" },
      },
      // @ts-expect-error — hostile payload smuggling extra keys past the contract
      input: { requester_email: "a@b.co" },
    } as EvaluationRunComparison;

    const summary = comparisonSummary(polluted);
    const rows = metricComparisonRows(polluted.metrics);
    const identities = [
      ...identityRows(polluted.baseline),
      ...identityRows(polluted.candidate),
    ];

    const serialized = JSON.stringify({ summary, rows, identities });
    assert.equal(serialized.includes("a@b.co"), false);
    assert.equal(serialized.includes("system: you are"), false);
    assert.equal(serialized.includes("How do I reset my password"), false);
    assert.deepEqual(Object.keys(summary).sort(), [
      "baselinePassRate",
      "candidatePassRate",
      "deltaLabel",
    ]);
  });
});

describe("start evaluation capability gate", () => {
  it("evaluation.read alone hides the start buttons", () => {
    assert.equal(canManageEvaluations(canFrom([CAPABILITIES.EVALUATION_READ])), false);
  });

  it("evaluation.manage reveals the start buttons", () => {
    assert.equal(canManageEvaluations(canFrom([CAPABILITIES.EVALUATION_MANAGE])), true);
  });

  it("evaluation.read + evaluation.manage reveals the start buttons", () => {
    assert.equal(
      canManageEvaluations(
        canFrom([CAPABILITIES.EVALUATION_READ, CAPABILITIES.EVALUATION_MANAGE]),
      ),
      true,
    );
  });

  it("an unrelated capability never reveals the start buttons", () => {
    assert.equal(canManageEvaluations(canFrom([CAPABILITIES.TICKET_READ])), false);
    assert.equal(canManageEvaluations(canFrom([])), false);
  });
});

function recordingRequestFetcher(calls: RequestInfo[]) {
  return async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    calls.push(input);
    if (init?.method === "POST") {
      return jsonResponse({
        run_id: "run-new",
        target_type: "rag",
        status: "queued",
        model: "gpt-4o",
        job_id: 7,
        job_status: "queued",
      });
    }
    return jsonResponse({ items: [], total: 0 });
  };
}

function postedJsonBody(init: RequestInit | undefined) {
  assert.ok(init?.method === "POST");
  assert.ok(init?.body);
  return JSON.parse(String(init.body));
}

describe("start RAG evaluation", () => {
  it("posts to the RAG run URL", async () => {
    const calls: RequestInfo[] = [];
    const fetcher = recordingRequestFetcher(calls) as typeof fetch;
    await startRagEvaluation(
      [
        {
          id: "rag-001",
          question: "What refund options are available?",
          expected_sources: ["KB-101"],
          expected_terms: ["refund"],
          should_refuse: false,
        },
      ],
      fetcher,
    );
    assert.deepEqual(calls.map(String), [
      "/api/backend/evaluations/runs/rag",
    ]);
  });

  it("sends only safe supported fields with no organization id or PII", async () => {
    const calls: RequestInfo[] = [];
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(input);
      Object.assign(init, opts);
      return jsonResponse({
        run_id: "run-new",
        target_type: "rag",
        status: "queued",
        model: "gpt-4o",
        job_id: 7,
        job_status: "queued",
      });
    }) as typeof fetch;

    await startRagEvaluation(
      [
        {
          id: "rag-001",
          question: "What refund options are available?",
          expected_sources: ["KB-101", "KB-204"],
          expected_terms: ["refund", "30 days"],
          should_refuse: true,
        },
      ],
      fetcher,
    );

    const body = postedJsonBody(init);
    assert.equal(body.cases.length, 1);
    assert.deepEqual(body.cases[0], {
      id: "rag-001",
      question: "What refund options are available?",
      expected_sources: ["KB-101", "KB-204"],
      expected_terms: ["refund", "30 days"],
      should_refuse: true,
    });
    assert.equal("organization_id" in body, false);
    assert.equal("customer_email" in body, false);
    assert.equal("prompt" in body, false);
    assert.equal("ticket_body" in body, false);
    assert.equal("secrets" in body, false);
    const serialized = JSON.stringify(body);
    assert.equal(serialized.includes("organization_id"), false);
  });
});

describe("agent intent expectation options", () => {
  it("mirrors the backend AgentIntent literal plus the no-expectation option", () => {
    assert.deepEqual(AGENT_INTENT_OPTIONS, [
      { value: "", label: "Not specified" },
      { value: "information", label: "Information" },
      { value: "action", label: "Action" },
      { value: "mixed", label: "Mixed" },
      { value: "none", label: "None" },
    ]);
  });

  it("exposes only the four backend labels as selectable intents", () => {
    const values = AGENT_INTENT_OPTIONS.map((option) => option.value);
    assert.equal(
      values
        .filter((value) => value !== "")
        .every((value) =>
          ["information", "action", "mixed", "none"].includes(value),
        ),
      true,
    );
  });
});

describe("specialist path expectation presets", () => {
  it("mirrors the backend specialist literals as two fixed presets plus none", () => {
    assert.deepEqual(SPECIALIST_PATH_PRESETS, [
      { value: "", label: "Not specified", path: null },
      {
        value: "action",
        label: "Coordinator → Action",
        path: ["coordinator", "action"],
      },
      {
        value: "full",
        label: "Coordinator → Knowledge → Action",
        path: ["coordinator", "knowledge", "action"],
      },
    ]);
  });

  it("resolves only bounded backend literals and never free text", () => {
    assert.deepEqual(specialistPathForPreset("action"), [
      "coordinator",
      "action",
    ]);
    assert.deepEqual(specialistPathForPreset("full"), [
      "coordinator",
      "knowledge",
      "action",
    ]);

    // "Not specified" and any unknown value yield no expectation.
    assert.equal(specialistPathForPreset(""), null);
    assert.equal(specialistPathForPreset("refund my account"), null);
    assert.equal(specialistPathForPreset("intruder"), null);
  });
});

describe("start Agent evaluation", () => {
  it("posts to the Agent run URL", async () => {
    const calls: RequestInfo[] = [];
    const fetcher = recordingRequestFetcher(calls) as typeof fetch;
    await startAgentEvaluation(
      [
        {
          ticket_id: 4201,
          expected_action: "refund",
          expected_retrieval: true,
          expected_tool: "process_refund",
          expected_auto_execute: false,
        },
      ],
      fetcher,
    );
    assert.deepEqual(calls.map(String), [
      "/api/backend/evaluations/runs/agent",
    ]);
  });

  it("sends only safe supported fields with no organization id or PII", async () => {
    const calls: RequestInfo[] = [];
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(input);
      Object.assign(init, opts);
      return jsonResponse({
        run_id: "run-agent",
        target_type: "agent",
        status: "queued",
        model: "gpt-4o",
        job_id: 8,
        job_status: "queued",
      });
    }) as typeof fetch;

    await startAgentEvaluation(
      [
        {
          ticket_id: 4201,
          expected_action: "refund",
          expected_retrieval: true,
          expected_tool: "process_refund",
          expected_auto_execute: false,
        },
      ],
      fetcher,
    );

    const body = postedJsonBody(init);
    assert.equal(body.cases.length, 1);
    assert.deepEqual(body.cases[0], {
      ticket_id: 4201,
      expected_action: "refund",
      expected_retrieval: true,
      expected_tool: "process_refund",
      expected_auto_execute: false,
    });
    assert.equal(JSON.stringify(body).includes("customer_email"), false);
    assert.equal(JSON.stringify(body).includes("organization_id"), false);
    assert.equal(JSON.stringify(body).includes("ticket_body"), false);
  });

  it("sends fingerprint only when supplied", async () => {
    const calls: RequestInfo[] = [];
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(input);
      Object.assign(init, opts);
      return jsonResponse({
        run_id: "run-agent",
        target_type: "agent",
        status: "queued",
        model: "gpt-4o",
        job_id: 8,
        job_status: "queued",
      });
    }) as typeof fetch;

    await startAgentEvaluation(
      [
        {
          ticket_id: 1,
          expected_action: "x",
          expected_retrieval: false,
          expected_tool: "y",
          expected_auto_execute: false,
          fingerprint: "agent-case-001",
        },
      ],
      fetcher,
    );
    const body = postedJsonBody(init);
    assert.equal(body.cases[0].fingerprint, "agent-case-001");
  });

  it("sends expected_intent only when supplied", async () => {
    const calls: RequestInfo[] = [];
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(input);
      Object.assign(init, opts);
      return jsonResponse({
        run_id: "run-agent",
        target_type: "agent",
        status: "queued",
        model: "gpt-4o",
        job_id: 8,
        job_status: "queued",
      });
    }) as typeof fetch;

    await startAgentEvaluation(
      [
        {
          ticket_id: 1,
          expected_action: "x",
          expected_retrieval: false,
          expected_tool: "y",
          expected_auto_execute: false,
          expected_intent: "action",
        },
      ],
      fetcher,
    );
    assert.equal(postedJsonBody(init).cases[0].expected_intent, "action");
  });

  it("omits expected_intent entirely when not specified", async () => {
    const calls: RequestInfo[] = [];
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(input);
      Object.assign(init, opts);
      return jsonResponse({
        run_id: "run-agent",
        target_type: "agent",
        status: "queued",
        model: "gpt-4o",
        job_id: 8,
        job_status: "queued",
      });
    }) as typeof fetch;

    await startAgentEvaluation(
      [
        {
          ticket_id: 1,
          expected_action: "x",
          expected_retrieval: false,
          expected_tool: "y",
          expected_auto_execute: false,
        },
      ],
      fetcher,
    );
    const body = postedJsonBody(init);
    assert.equal("expected_intent" in body.cases[0], false);
    assert.equal(JSON.stringify(body).includes("expected_intent"), false);
  });

  it("sends expected_specialists only when supplied", async () => {
    const calls: RequestInfo[] = [];
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(input);
      Object.assign(init, opts);
      return jsonResponse({
        run_id: "run-agent",
        target_type: "agent",
        status: "queued",
        model: "gpt-4o",
        job_id: 8,
        job_status: "queued",
      });
    }) as typeof fetch;

    await startAgentEvaluation(
      [
        {
          ticket_id: 1,
          expected_action: "x",
          expected_retrieval: false,
          expected_tool: "y",
          expected_auto_execute: false,
          expected_specialists: ["coordinator", "knowledge", "action"],
        },
      ],
      fetcher,
    );
    assert.deepEqual(postedJsonBody(init).cases[0].expected_specialists, [
      "coordinator",
      "knowledge",
      "action",
    ]);
  });

  it("omits expected_specialists entirely when not specified", async () => {
    const calls: RequestInfo[] = [];
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(input);
      Object.assign(init, opts);
      return jsonResponse({
        run_id: "run-agent",
        target_type: "agent",
        status: "queued",
        model: "gpt-4o",
        job_id: 8,
        job_status: "queued",
      });
    }) as typeof fetch;

    await startAgentEvaluation(
      [
        {
          ticket_id: 1,
          expected_action: "x",
          expected_retrieval: false,
          expected_tool: "y",
          expected_auto_execute: false,
        },
      ],
      fetcher,
    );
    const body = postedJsonBody(init);
    assert.equal("expected_specialists" in body.cases[0], false);
    assert.equal(
      JSON.stringify(body).includes("expected_specialists"),
      false,
    );
  });
});

describe("queue evaluation success flow", () => {
  it("shows the queued message constant on success", () => {
    assert.equal(EVALUATION_QUEUED_MESSAGE, "Evaluation queued.");
  });

  it("queues a RAG run then calls the runs GET again", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${opts?.method ?? "GET"}`);
      if (opts?.method === "POST") {
        return jsonResponse({
          run_id: "run-new",
          target_type: "rag",
          status: "queued",
          model: "gpt-4o",
          job_id: 7,
          job_status: "queued",
        });
      }
      return jsonResponse({
        items: [
          { ...RUN, run_id: "run-new", pass_rate: null, completed_at: null },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      });
    }) as typeof fetch;

    const outcome = await queueRagEvaluation(
      [{ id: "rag-001", question: "q?", expected_sources: [], expected_terms: [], should_refuse: false }],
      fetcher,
    );

    assert.deepEqual(calls, [
      "/api/backend/evaluations/runs/rag:POST",
      "/api/backend/evaluations/runs?limit=50&offset=0:GET",
    ]);
    assert.equal(outcome.queued.job_status, "queued");
    assert.equal(outcome.runs.items[0].run_id, "run-new");
  });

  it("queues an Agent run then calls the runs GET again", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${opts?.method ?? "GET"}`);
      if (opts?.method === "POST") {
        return jsonResponse({
          run_id: "run-agent",
          target_type: "agent",
          status: "queued",
          model: "gpt-4o",
          job_id: 8,
          job_status: "queued",
        });
      }
      return jsonResponse({ items: [], total: 0, limit: 50, offset: 0 });
    }) as typeof fetch;

    const outcome = await queueAgentEvaluation(
      [
        {
          ticket_id: 1,
          expected_action: "x",
          expected_retrieval: false,
          expected_tool: "y",
          expected_auto_execute: false,
        },
      ],
      fetcher,
    );

    assert.deepEqual(calls, [
      "/api/backend/evaluations/runs/agent:POST",
      "/api/backend/evaluations/runs?limit=50&offset=0:GET",
    ]);
    assert.equal(outcome.queued.target_type, "agent");
  });

  it("does not call the runs GET again when the POST fails", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${opts?.method ?? "GET"}`);
      return jsonResponse({ detail: "boom" }, 403);
    }) as typeof fetch;

    await assert.rejects(
      () =>
        queueRagEvaluation(
          [{ id: "r", question: "q", expected_sources: [], expected_terms: [], should_refuse: false }],
          fetcher,
        ),
      /Could not start evaluation/,
    );
    assert.deepEqual(calls, ["/api/backend/evaluations/runs/rag:POST"]);
  });
});

describe("start evaluation error flow", () => {
  it("shows only the safe generic error on failure", async () => {
    assert.equal(EVALUATION_START_ERROR, "Could not start evaluation.");

    const fetcher = (async (): Promise<Response> =>
      jsonResponse({ detail: "Internal server error: openai key expired" }, 500)) as typeof fetch;

    await assert.rejects(
      () =>
        startRagEvaluation(
          [{ id: "r", question: "q", expected_sources: [], expected_terms: [], should_refuse: false }],
          fetcher,
        ),
      /Could not start evaluation/,
    );

    await assert.rejects(
      () =>
        startAgentEvaluation(
          [
            {
              ticket_id: 1,
              expected_action: "x",
              expected_retrieval: false,
              expected_tool: "y",
              expected_auto_execute: false,
            },
          ],
          fetcher,
        ),
      /Could not start evaluation/,
    );
  });
});

describe("promote to baseline visibility", () => {
  it("evaluation.read alone hides the promote button", () => {
    assert.equal(canManageEvaluations(canFrom([CAPABILITIES.EVALUATION_READ])), false);
    assert.equal(canPromoteRunToBaseline(SUCCEEDED_RUN), true);
  });

  it("evaluation.manage makes the button visible on a succeeded run", () => {
    assert.equal(canManageEvaluations(canFrom([CAPABILITIES.EVALUATION_MANAGE])), true);
    assert.equal(canPromoteRunToBaseline(SUCCEEDED_RUN), true);
  });

  it("hides the button on a queued run", () => {
    assert.equal(canPromoteRunToBaseline({ ...RUN, status: "queued" }), false);
  });

  it("hides the button on a running run", () => {
    assert.equal(canPromoteRunToBaseline({ ...RUN, status: "running" }), false);
  });

  it("hides the button on a failed run", () => {
    assert.equal(canPromoteRunToBaseline({ ...RUN, status: "failed" }), false);
  });
});

describe("promote to baseline POST helper", () => {
  it("posts to the run baseline URL", async () => {
    const calls: RequestInfo[] = [];
    const fetcher = (async (input: RequestInfo | URL): Promise<Response> => {
      calls.push(input);
      return jsonResponse({
        ...BASELINE,
        id: 8,
        version: "v3",
      });
    }) as typeof fetch;
    const baseline = await createRunBaseline("run-abc", fetcher);
    assert.deepEqual(calls.map(String), [
      "/api/backend/evaluations/runs/run-abc/baseline",
    ]);
    assert.equal(baseline.id, 8);
  });

  it("posts only a POST with no body and no custom headers", async () => {
    const calls: string[] = [];
    let init: RequestInit | undefined;
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(String(input));
      init = opts;
      return jsonResponse(BASELINE);
    }) as typeof fetch;
    await createRunBaseline("a/b c", fetcher);
    assert.equal(init?.method, "POST");
    assert.equal(init?.body, undefined);
    const serialized = JSON.stringify(init);
    assert.equal(serialized.includes("organization_id"), false);
    assert.equal(serialized.includes("created_by"), false);
    assert.equal(serialized.includes("metrics"), false);
    assert.equal(serialized.includes("customer"), false);
  });

  it("throws the safe error when the backend rejects the POST", async () => {
    const fetcher: typeof fetch = async () =>
      jsonResponse({ detail: "run is not succeeded" }, 400);
    await assert.rejects(
      () => createRunBaseline("run-abc", fetcher),
      /Could not create baseline/,
    );
  });
});

describe("promote to baseline flow", () => {
  it("posts then refreshes the baseline list for the run target type", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${opts?.method ?? "GET"}`);
      if (opts?.method === "POST") {
        return jsonResponse({ ...BASELINE, id: 8, version: "v3" });
      }
      return jsonResponse({ items: [BASELINE, { ...BASELINE, id: 8 }], total: 2 });
    }) as typeof fetch;

    const outcome = await promoteRunToBaseline(SUCCEEDED_RUN, fetcher);
    assert.deepEqual(calls, [
      "/api/backend/evaluations/runs/run-abc/baseline:POST",
      "/api/backend/evaluations/baselines?target_type=rag&limit=50:GET",
    ]);
    assert.equal(outcome.baseline.id, 8);
    assert.equal(outcome.baselines.items.length, 2);
  });

  it("shows the success message on a successful promotion", async () => {
    assert.equal(EVALUATION_PROMOTE_SUCCESS, "Baseline created.");
  });

  it("does not call the baselines GET again when the POST fails", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${opts?.method ?? "GET"}`);
      return jsonResponse({ detail: "boom" }, 500);
    }) as typeof fetch;

    await assert.rejects(
      () => promoteRunToBaseline(SUCCEEDED_RUN, fetcher),
      /Could not create baseline/,
    );
    assert.deepEqual(calls, [
      "/api/backend/evaluations/runs/run-abc/baseline:POST",
    ]);
  });

  it("shows only the safe generic error on failure", async () => {
    assert.equal(EVALUATION_PROMOTE_ERROR, "Could not create baseline.");
    const fetcher: typeof fetch = async () =>
      jsonResponse({ detail: "Internal server error: db connection lost" }, 500);
    await assert.rejects(
      () => promoteRunToBaseline(SUCCEEDED_RUN, fetcher),
      /Could not create baseline/,
    );
  });

  it("guard prevents a second POST while the first is still in flight", async () => {
    const calls: RequestInfo[] = [];
    let release: ((value: Response) => void) | undefined;
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(input);
      if (opts?.method === "POST") {
        return await new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      return jsonResponse({ items: [BASELINE], total: 1 });
    }) as typeof fetch;

    let promoting = false;
    const guardedPromote = async () => {
      if (promoting) {
        return;
      }
      promoting = true;
      try {
        await promoteRunToBaseline(SUCCEEDED_RUN, fetcher);
      } finally {
        promoting = false;
      }
    };

    const first = guardedPromote();
    const second = guardedPromote();
    release?.(jsonResponse(BASELINE));
    await first;
    await second;

    assert.deepEqual(
      calls
        .map(String)
        .filter((call) => call.endsWith("/baseline")),
      ["/api/backend/evaluations/runs/run-abc/baseline"],
    );
    assert.equal(EVALUATION_PROMOTING_LABEL, "Creating...");
  });
});

const DECISION: EvaluationReleaseDecisionRecord = {
  id: 21,
  organization_id: 1,
  candidate_run_id: 1,
  baseline_id: 7,
  decision: "approved",
  decided_by_subject: "auth:11",
  note: "Retrieval improved.",
  comparison_snapshot: { candidate_pass_rate: 0.9, pass_rate_delta: 0.05 },
  created_at: "2026-09-24T11:00:00Z",
};

describe("release decision visibility", () => {
  it("hides Approve/Reject for evaluation.read only", () => {
    assert.equal(
      canShowReleaseDecisionActions({
        can: canFrom([CAPABILITIES.EVALUATION_READ]),
        run: SUCCEEDED_RUN,
        hasSelectedBaseline: true,
        hasComparison: true,
      }),
      false,
    );
  });

  it("shows Approve/Reject with evaluation.manage + a loaded comparison", () => {
    assert.equal(
      canShowReleaseDecisionActions({
        can: canFrom([CAPABILITIES.EVALUATION_MANAGE]),
        run: SUCCEEDED_RUN,
        hasSelectedBaseline: true,
        hasComparison: true,
      }),
      true,
    );
  });

  it("hides the actions before the comparison has loaded", () => {
    assert.equal(
      canShowReleaseDecisionActions({
        can: canFrom([CAPABILITIES.EVALUATION_MANAGE]),
        run: SUCCEEDED_RUN,
        hasSelectedBaseline: true,
        hasComparison: false,
      }),
      false,
    );
    assert.equal(
      canShowReleaseDecisionActions({
        can: canFrom([CAPABILITIES.EVALUATION_MANAGE]),
        run: SUCCEEDED_RUN,
        hasSelectedBaseline: false,
        hasComparison: true,
      }),
      false,
    );
  });

  it("hides the actions for a queued, running, or failed candidate", () => {
    for (const status of ["queued", "running", "failed"]) {
      assert.equal(
        canShowReleaseDecisionActions({
          can: canFrom([CAPABILITIES.EVALUATION_MANAGE]),
          run: { ...RUN, status },
          hasSelectedBaseline: true,
          hasComparison: true,
        }),
        false,
        `status ${status} must not be decidable`,
      );
    }
  });

  it("hides the actions when no run is selected", () => {
    assert.equal(
      canShowReleaseDecisionActions({
        can: canFrom([CAPABILITIES.EVALUATION_MANAGE]),
        run: null,
        hasSelectedBaseline: true,
        hasComparison: true,
      }),
      false,
    );
  });
});

describe("release decision POST helper", () => {
  it("approve posts to the run release-decision URL", async () => {
    const calls: RequestInfo[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
    ): Promise<Response> => {
      calls.push(input);
      return jsonResponse(DECISION);
    }) as typeof fetch;
    const decision = await recordReleaseDecision(
      "run-abc",
      { baseline_id: 7, decision: "approved", note: "Retrieval improved." },
      fetcher,
    );
    assert.deepEqual(calls.map(String), [
      "/api/backend/evaluations/runs/run-abc/release-decision",
    ]);
    assert.equal(decision.id, 21);
  });

  it("approve sends baseline_id, decision=approved, and the note", async () => {
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      Object.assign(init, opts);
      return jsonResponse(DECISION);
    }) as typeof fetch;

    await recordReleaseDecision(
      "run-abc",
      { baseline_id: 7, decision: "approved", note: "Retrieval improved." },
      fetcher,
    );
    assert.deepEqual(postedJsonBody(init), {
      baseline_id: 7,
      decision: "approved",
      note: "Retrieval improved.",
    });
  });

  it("reject sends decision=rejected", async () => {
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      Object.assign(init, opts);
      return jsonResponse({ ...DECISION, decision: "rejected" });
    }) as typeof fetch;

    await recordReleaseDecision(
      "run-abc",
      { baseline_id: 7, decision: "rejected", note: "Missed grounding." },
      fetcher,
    );
    assert.equal(postedJsonBody(init).decision, "rejected");
  });

  it("never sends organization_id, decided_by_subject, or comparison_snapshot", async () => {
    const init: RequestInit = {};
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      Object.assign(init, opts);
      return jsonResponse(DECISION);
    }) as typeof fetch;

    await recordReleaseDecision(
      "run-abc",
      { baseline_id: 7, decision: "approved" },
      fetcher,
    );
    const serialized = JSON.stringify(postedJsonBody(init));
    assert.equal(serialized.includes("organization_id"), false);
    assert.equal(serialized.includes("decided_by_subject"), false);
    assert.equal(serialized.includes("comparison_snapshot"), false);
    assert.equal(serialized.includes("candidate_pass_rate"), false);
    assert.equal(serialized.includes("run_id"), false);
  });

  it("throws the safe error when the backend rejects the decision", async () => {
    const fetcher: typeof fetch = async () =>
      jsonResponse({ detail: "run is not succeeded" }, 400);
    await assert.rejects(
      () =>
        recordReleaseDecision("run-abc", {
          baseline_id: 7,
          decision: "approved",
        }, fetcher),
      /Could not save decision/,
    );
  });

  it("shows only the safe error label on any failure", async () => {
    assert.equal(EVALUATION_DECISION_ERROR, "Could not save decision.");
    const fetcher: typeof fetch = async () =>
      jsonResponse({ detail: "Internal server error: eval key expired" }, 500);
    await assert.rejects(
      () =>
        recordReleaseDecision("run-abc", {
          baseline_id: 7,
          decision: "rejected",
        }, fetcher),
      /Could not save decision/,
    );
  });
});

describe("release decision blocked approval", () => {
  const blockedFetcher = (): typeof fetch =>
    (async (): Promise<Response> =>
      jsonResponse(
        { detail: "Approval blocked by critical evaluation regression." },
        409,
      )) as typeof fetch;

  it("shows the safe blocked-approval message, distinct from the generic error", () => {
    assert.equal(
      EVALUATION_DECISION_BLOCKED_MESSAGE,
      "Approval blocked because a critical safety metric regressed.",
    );
    assert.notEqual(
      EVALUATION_DECISION_BLOCKED_MESSAGE,
      EVALUATION_DECISION_ERROR,
    );
  });

  it("throws the blocked-approval sentinel when the backend returns 409 on approve", async () => {
    let caught: unknown;
    try {
      await recordReleaseDecision(
        "run-abc",
        { baseline_id: 7, decision: "approved" },
        blockedFetcher(),
      );
    } catch (err) {
      caught = err;
    }
    assert.ok(caught instanceof ReleaseDecisionBlockedError);
    assert.equal(
      (caught as Error).message,
      EVALUATION_DECISION_BLOCKED_MESSAGE,
    );
  });

  it("never triggers the history GET when an approval is blocked", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${opts?.method ?? "GET"}`);
      return jsonResponse(
        { detail: "Approval blocked by critical evaluation regression." },
        409,
      );
    }) as typeof fetch;

    await assert.rejects(
      () =>
        reviewReleaseDecision(
          SUCCEEDED_RUN,
          { baseline_id: 7, decision: "approved" },
          fetcher,
        ),
      ReleaseDecisionBlockedError,
    );
    assert.deepEqual(calls, [
      "/api/backend/evaluations/runs/run-abc/release-decision:POST",
    ]);
  });

  it("keeps every other failure on the generic 'Could not save decision.' label", async () => {
    const fetcher500 = (async (): Promise<Response> =>
      jsonResponse({ detail: "Internal server error: eval key expired" }, 500)) as typeof fetch;

    let caught: unknown;
    try {
      await recordReleaseDecision(
        "run-abc",
        { baseline_id: 7, decision: "approved" },
        fetcher500,
      );
    } catch (err) {
      caught = err;
    }
    assert.ok(!(caught instanceof ReleaseDecisionBlockedError));
    assert.equal((caught as Error).message, EVALUATION_DECISION_ERROR);
  });

  it("never leaks the raw 409 payload into the blocked-approval message", async () => {
    const fetcher = (async (): Promise<Response> =>
      jsonResponse(
        {
          detail:
            "Approval blocked by critical evaluation regression. token=sekrit",
        },
        409,
      )) as typeof fetch;

    let caught: unknown;
    try {
      await recordReleaseDecision(
        "run-abc",
        { baseline_id: 7, decision: "approved" },
        fetcher,
      );
    } catch (err) {
      caught = err;
    }
    assert.ok(caught instanceof ReleaseDecisionBlockedError);
    const serialized = JSON.stringify(caught);
    assert.equal(serialized.includes("critical evaluation regression"), false);
    assert.equal(serialized.includes("sekrit"), false);
    assert.equal(
      EVALUATION_DECISION_BLOCKED_MESSAGE.includes("critical evaluation"),
      false,
    );
  });

  it("uses the Rejected. label, untouched by the blocked-approval path", async () => {
    assert.equal(EVALUATION_DECISION_REJECTED_MESSAGE, "Rejected.");
    assert.equal(
      EVALUATION_DECISION_BLOCKED_MESSAGE.includes("Rejected."),
      false,
    );
  });
});

describe("release decision history GET helper", () => {
  it("requests the bounded history list with limit=50&offset=0 by default", async () => {
    const urls: string[] = [];
    await fetchReleaseDecisions({}, recordingFetcher(urls) as typeof fetch);
    assert.deepEqual(urls, [
      "/api/backend/evaluations/release-decisions?limit=50&offset=0",
    ]);
  });

  it("forwards a custom limit and offset", async () => {
    const urls: string[] = [];
    await fetchReleaseDecisions(
      { limit: 20, offset: 40 },
      recordingFetcher(urls) as typeof fetch,
    );
    assert.deepEqual(urls, [
      "/api/backend/evaluations/release-decisions?limit=20&offset=40",
    ]);
  });

  it("parses a history list response", async () => {
    const fetcher: typeof fetch = async () =>
      jsonResponse({ items: [DECISION], total: 1 });
    const response = await fetchReleaseDecisions({}, fetcher);
    assert.equal(response.total, 1);
    assert.equal(response.items[0].decision, "approved");
  });

  it("throws the safe error when the history fetch is not ok", async () => {
    const fetcher: typeof fetch = async () => jsonResponse({}, 500);
    await assert.rejects(
      () => fetchReleaseDecisions({}, fetcher),
      /Could not load release decisions/,
    );
  });
});

describe("release decision success flow", () => {
  it("records the decision then fetches the history again", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${opts?.method ?? "GET"}`);
      if (opts?.method === "POST") {
        return jsonResponse(DECISION);
      }
      return jsonResponse({ items: [DECISION], total: 1 });
    }) as typeof fetch;

    const outcome = await reviewReleaseDecision(
      SUCCEEDED_RUN,
      { baseline_id: 7, decision: "approved" },
      fetcher,
    );
    assert.deepEqual(calls, [
      "/api/backend/evaluations/runs/run-abc/release-decision:POST",
      "/api/backend/evaluations/release-decisions?limit=50&offset=0:GET",
    ]);
    assert.equal(outcome.decision.id, 21);
    assert.equal(outcome.history.items.length, 1);
  });

  it("does not call the history GET when the POST fails", async () => {
    const calls: string[] = [];
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      calls.push(`${String(input)}:${opts?.method ?? "GET"}`);
      return jsonResponse({ detail: "boom" }, 400);
    }) as typeof fetch;

    await assert.rejects(
      () =>
        reviewReleaseDecision(
          SUCCEEDED_RUN,
          { baseline_id: 7, decision: "rejected" },
          fetcher,
        ),
      /Could not save decision/,
    );
    assert.deepEqual(calls, [
      "/api/backend/evaluations/runs/run-abc/release-decision:POST",
    ]);
  });

  it("shows Approved. after a successful approve", () => {
    assert.equal(EVALUATION_DECISION_APPROVED_MESSAGE, "Approved.");
  });

  it("shows Rejected. after a successful reject", () => {
    assert.equal(EVALUATION_DECISION_REJECTED_MESSAGE, "Rejected.");
  });

  it("guard prevents a second POST while the first is still in flight", async () => {
    const calls: RequestInfo[] = [];
    let release: ((value: Response) => void) | undefined;
    const fetcher = (async (
      input: RequestInfo | URL,
      opts?: RequestInit,
    ): Promise<Response> => {
      if (opts?.method === "POST") {
        calls.push(input);
        return await new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      return jsonResponse({ items: [DECISION], total: 1 });
    }) as typeof fetch;

    let saving = false;
    const guardedReview = async () => {
      if (saving) {
        return;
      }
      saving = true;
      try {
        await reviewReleaseDecision(
          SUCCEEDED_RUN,
          { baseline_id: 7, decision: "approved" },
          fetcher,
        );
      } finally {
        saving = false;
      }
    };

    const first = guardedReview();
    const second = guardedReview();
    release?.(jsonResponse(DECISION));
    await first;
    await second;

    assert.deepEqual(calls.map(String), [
      "/api/backend/evaluations/runs/run-abc/release-decision",
    ]);
    assert.equal(EVALUATION_DECISION_SAVING_LABEL, "Saving...");
  });
});

describe("release decision history rendering", () => {
  it("renders Approved rows with reviewer, note, and created time", () => {
    const row = releaseDecisionRow(DECISION);
    assert.equal(row.decisionLabel, "Approved");
    assert.equal(row.candidateRunId, 1);
    assert.equal(row.baselineId, 7);
    assert.equal(row.reviewer, "auth:11");
    assert.equal(row.note, "Retrieval improved.");
    assert.equal(row.createdLabel === "—", false);
  });

  it("renders Rejected rows verbatim", () => {
    assert.equal(decisionLabel("rejected"), "Rejected");
    const row = releaseDecisionRow({
      ...DECISION,
      decision: "rejected",
      note: null,
    });
    assert.equal(row.decisionLabel, "Rejected");
    assert.equal(row.note, null);
  });

  it("never exposes the comparison snapshot contents in a row", () => {
    const row = releaseDecisionRow(DECISION);
    assert.equal(
      JSON.stringify(row).includes("candidate_pass_rate"),
      false,
    );
    assert.equal(row.decisionLabel, "Approved");
  });

  it("keeps the decision constant within the note limit", () => {
    assert.equal(EVALUATION_DECISION_NOTE_MAX, 1000);
    assert.equal(EVALUATION_DECISIONS_LOAD_ERROR, "Could not load release decisions.");
  });
});