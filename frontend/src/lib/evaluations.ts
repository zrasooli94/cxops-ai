/**
 * Read-only frontend helpers for the AI evaluations control-center page.
 *
 * Only the safe GET endpoints are represented here (runs list, run detail,
 * run cases, baseline list, run-to-baseline comparison). No POST/PATCH call
 * is defined for creating baselines or mutating runs — the compare-with-
 * baseline UI is read-only, and the frontend never decides authorisation —
 * FastAPI authorizes every request.
 *
 * PII safety: run inputs are snapshots of the raw ticket/conversation/prompt
 * and are never exposed by the backend read responses. The types below mirror
 * only the safe fields the backend serialises, so a sensitive-looking key that
 * is not part of this contract is simply not rendered.
 */
import { CAPABILITIES, type Capability } from "./authorization/capabilities.ts";

export const EVALUATIONS_RUNS_PATH = "/api/backend/evaluations/runs";
export const EVALUATIONS_BASELINES_PATH = "/api/backend/evaluations/baselines";
export const EVALUATIONS_DEFAULT_LIMIT = 50;
export const EVALUATIONS_DEFAULT_OFFSET = 0;

/** Safe error label shown when the baseline list cannot be loaded. */
export const EVALUATION_BASELINES_ERROR = "Could not load baselines.";

/** Safe error label shown when comparing a run to a baseline fails. */
export const EVALUATION_COMPARE_ERROR = "Could not compare evaluation.";

/** Success banner shown after a run is queued. */
export const EVALUATION_QUEUED_MESSAGE = "Evaluation queued.";

/** Safe error label shown when starting a run fails. */
export const EVALUATION_START_ERROR = "Could not start evaluation.";

export type EvaluationTargetType = "rag" | "agent" | "repeatability" | "latency";
export type EvaluationRunStatus = "queued" | "running" | "succeeded" | "failed";

/** Mirrors `EvaluationRunRead` — the backend never includes the run input here. */
export interface EvaluationRun {
  id: number;
  run_id: string;
  organization_id: number;
  target_type: string;
  status: string;
  model: string;
  embedding_model: string | null;
  agent_decision_version: string | null;
  tool_policy_version: number | null;
  corpus_revision: Record<string, unknown> | null;
  trigger_source: string;
  requested_by_subject: string | null;
  pass_rate: number | null;
  metrics: Record<string, unknown>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

/** Mirrors `EvaluationRunListResponse`. */
export interface EvaluationRunListResponse {
  items: EvaluationRun[];
  total: number;
  limit: number;
  offset: number;
}

/** Mirrors `EvaluationCaseRead`. `input` is intentionally not modelled. */
export interface EvaluationCaseResult {
  id: number;
  run_id: number;
  organization_id: number;
  case_id: string;
  case_type: string;
  expected: Record<string, unknown>;
  actual: Record<string, unknown>;
  dimensions: Record<string, unknown>;
  latency_ms: number | null;
  total_tokens: number | null;
  estimated_cost_usd: number | null;
  fingerprint: string | null;
  created_at: string;
}

/** Mirrors `EvaluationCaseListResponse`. */
export interface EvaluationCaseListResponse {
  items: EvaluationCaseResult[];
  total: number;
}

/** Mirrors `EvaluationBaselineRead`. */
export interface EvaluationBaseline {
  id: number;
  organization_id: number;
  target_type: string;
  version: string;
  model: string;
  embedding_model: string | null;
  agent_decision_version: string | null;
  tool_policy_version: number | null;
  corpus_revision: Record<string, unknown> | null;
  pass_rate: number | null;
  metrics: Record<string, unknown>;
  cases_count: number;
  created_by_subject: string | null;
  promoted: boolean;
  created_at: string;
}

/** Mirrors `EvaluationBaselineListResponse`. */
export interface EvaluationBaselineListResponse {
  items: EvaluationBaseline[];
  total: number;
}

/** Mirrors `MetricDirection` — the backend decides, never the frontend. */
export type MetricDirection = "improved" | "regressed" | "same";

/** Mirrors `EvaluationIdentitySnapshot` — version identity, never judged. */
export interface EvaluationIdentitySnapshot {
  model: string;
  embedding_model: string | null;
  agent_decision_version: string | null;
  tool_policy_version: number | null;
  corpus_revision: Record<string, unknown> | null;
}

/** Mirrors `EvaluationMetricComparison`. */
export interface EvaluationMetricComparison {
  metric: string;
  baseline: number;
  candidate: number;
  delta: number;
  direction: MetricDirection;
}

/** Mirrors `EvaluationRunComparison`. */
export interface EvaluationRunComparison {
  target_type: string;
  candidate_pass_rate: number | null;
  baseline_pass_rate: number | null;
  pass_rate_delta: number | null;
  metrics: EvaluationMetricComparison[];
  baseline: EvaluationIdentitySnapshot;
  candidate: EvaluationIdentitySnapshot;
}

/**
 * A run conversation-derived key is deliberately absent from the run model,
 * run inputs, and case inputs responses. `evaluation.read` is the only
 * capability the page needs; starting new runs needs `evaluation.manage` and
 * is not part of this read-only page.
 */
export function canViewEvaluations(
  can: (capability: Capability) => boolean,
): boolean {
  return can(CAPABILITIES.EVALUATION_READ);
}

/**
 * Whether the subject may queue new evaluation runs. Start buttons are shown
 * only with `evaluation.manage`; `evaluation.read` alone keeps the page
 * read-only. No role-name checks.
 */
export function canManageEvaluations(
  can: (capability: Capability) => boolean,
): boolean {
  return can(CAPABILITIES.EVALUATION_MANAGE);
}

export interface EvaluationRunQuery {
  limit?: number;
  offset?: number;
}

function runsPath(query: EvaluationRunQuery): string {
  const limit = query.limit ?? EVALUATIONS_DEFAULT_LIMIT;
  const offset = query.offset ?? EVALUATIONS_DEFAULT_OFFSET;
  return `${EVALUATIONS_RUNS_PATH}?limit=${limit}&offset=${offset}`;
}

async function readJson<T>(response: Response, message: string): Promise<T> {
  if (!response.ok) {
    throw new Error(message);
  }
  return (await response.json()) as T;
}

/** GET /api/backend/evaluations/runs?limit=50&offset=0 */
export async function fetchEvaluationRuns(
  query: EvaluationRunQuery = {},
  fetcher: typeof fetch = fetch,
): Promise<EvaluationRunListResponse> {
  return readJson<EvaluationRunListResponse>(await fetcher(runsPath(query), {
    cache: "no-store",
  }), "Could not load evaluation data.");
}

/** GET /api/backend/evaluations/runs/{run_id} */
export async function fetchEvaluationRun(
  runId: string,
  fetcher: typeof fetch = fetch,
): Promise<EvaluationRun> {
  return readJson<EvaluationRun>(
    await fetcher(`${EVALUATIONS_RUNS_PATH}/${encodeURIComponent(runId)}`, {
      cache: "no-store",
    }),
    "Could not load evaluation data.",
  );
}

/** GET /api/backend/evaluations/runs/{run_id}/cases */
export async function fetchEvaluationRunCases(
  runId: string,
  fetcher: typeof fetch = fetch,
): Promise<EvaluationCaseListResponse> {
  return readJson<EvaluationCaseListResponse>(
    await fetcher(
      `${EVALUATIONS_RUNS_PATH}/${encodeURIComponent(runId)}/cases`,
      { cache: "no-store" },
    ),
    "Could not load evaluation data.",
  );
}

/**
 * The backend's target-type literal. Comparing against an unknown literal is a
 * 422, so baselines are only ever fetched for a type the backend accepts.
 */
const KNOWN_TARGET_TYPES: readonly string[] = [
  "rag",
  "agent",
  "repeatability",
  "latency",
];

export function isKnownEvaluationTarget(targetType: string): boolean {
  return KNOWN_TARGET_TYPES.includes(targetType);
}

export interface EvaluationBaselineQuery {
  targetType?: string;
  limit?: number;
}

function baselinesPath(query: EvaluationBaselineQuery): string {
  const limit = query.limit ?? EVALUATIONS_DEFAULT_LIMIT;
  const params = new URLSearchParams();
  if (query.targetType && isKnownEvaluationTarget(query.targetType)) {
    params.set("target_type", query.targetType);
  }
  params.set("limit", String(limit));
  return `${EVALUATIONS_BASELINES_PATH}?${params.toString()}`;
}

/** GET /api/backend/evaluations/baselines?target_type=&limit= */
export async function fetchEvaluationBaselines(
  query: EvaluationBaselineQuery = {},
  fetcher: typeof fetch = fetch,
): Promise<EvaluationBaselineListResponse> {
  return readJson<EvaluationBaselineListResponse>(
    await fetcher(baselinesPath(query), { cache: "no-store" }),
    EVALUATION_BASELINES_ERROR,
  );
}

/**
 * GET /api/backend/evaluations/runs/{run_id}/compare/{baseline_id}
 * Read-only comparison of one run against one baseline. Direction values are
 * computed and sent by the backend; the frontend never recalculates them.
 */
export async function compareRunToBaseline(
  runId: string,
  baselineId: number,
  fetcher: typeof fetch = fetch,
): Promise<EvaluationRunComparison> {
  return readJson<EvaluationRunComparison>(
    await fetcher(
      `${EVALUATIONS_RUNS_PATH}/${encodeURIComponent(runId)}/compare/${baselineId}`,
      { cache: "no-store" },
    ),
    EVALUATION_COMPARE_ERROR,
  );
}

/** Safe success message shown after a run is promoted to a baseline. */
export const EVALUATION_PROMOTE_SUCCESS = "Baseline created.";

/** Safe error label shown when promoting a run to a baseline fails. */
export const EVALUATION_PROMOTE_ERROR = "Could not create baseline.";

/** Button label shown while the promote request is in flight. */
export const EVALUATION_PROMOTING_LABEL = "Creating...";

/**
 * Whether a run is eligible to become a baseline. The backend stays
 * authoritative on success, but the button is hidden unless the run actually
 * succeeded so it is never shown for queued, running, or failed runs.
 */
export function canPromoteRunToBaseline(run: EvaluationRun): boolean {
  return run.status === "succeeded";
}

/**
 * POST /api/backend/evaluations/runs/{run_id}/baseline
 * Promotes a succeeded run to a baseline. Deliberately sends no request body:
 * the backend derives the organization and subject from the trusted auth
 * context, and the frontend never sends run data, metrics, or customer data.
 * Returns the created baseline.
 */
export async function createRunBaseline(
  runId: string,
  fetcher: typeof fetch = fetch,
): Promise<EvaluationBaseline> {
  const response = await fetcher(
    `${EVALUATIONS_RUNS_PATH}/${encodeURIComponent(runId)}/baseline`,
    { method: "POST", cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(EVALUATION_PROMOTE_ERROR);
  }
  return (await response.json()) as EvaluationBaseline;
}

export interface PromoteRunBaselineOutcome {
  baseline: EvaluationBaseline;
  baselines: EvaluationBaselineListResponse;
}

/**
 * Promote a succeeded run to a baseline, then fetch the refreshed baseline
 * list for the run's target type so the selector shows the new baseline at
 * once. The GET happens only after a successful POST.
 */
export async function promoteRunToBaseline(
  run: EvaluationRun,
  fetcher: typeof fetch = fetch,
): Promise<PromoteRunBaselineOutcome> {
  const baseline = await createRunBaseline(run.run_id, fetcher);
  const baselines = await fetchEvaluationBaselines(
    { targetType: run.target_type },
    fetcher,
  );
  return { baseline, baselines };
}

/**
 * One RAG evaluation case, mirroring `EvalRAGCaseInput`. Only the safe fields
 * the backend accepts are represented; no customer PII and no organization id
 * are ever sent from the frontend.
 */
export interface EvalRAGCaseInput {
  id: string;
  question: string;
  expected_sources: string[];
  expected_terms: string[];
  should_refuse: boolean;
  fingerprint?: string | null;
}

/**
 * One Agent evaluation case, mirroring `EvalAgentCaseInput`. Only the safe
 * fields the backend schema accepts are represented.
 */
export interface EvalAgentCaseInput {
  ticket_id: number;
  expected_action: string;
  expected_retrieval: boolean;
  expected_tool: string;
  expected_auto_execute: boolean;
  fingerprint?: string | null;
  /** Optional Coordinator-intent expectation (Phase 1K.2). Omit to skip. */
  expected_intent?: string | null;
  /**
   * Optional specialist-path expectation (Phase 1K.3). Only the bounded
   * backend literals coordinator/knowledge/action survive; the UI sends
   * presets only, never free text.
   */
  expected_specialists?: string[] | null;
}

/**
 * Specialist-path expectation presets. The empty value is "no expectation"
 * and is omitted from the payload so cases stay backward compatible with runs
 * that do not score the specialist path. The two non-empty presets are fixed
 * and bounded — there is no free-text path input, so unknown values can never
 * be sent.
 */
export const SPECIALIST_PATH_PRESETS: ReadonlyArray<{
  value: string;
  label: string;
  path: readonly string[] | null;
}> = [
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
];

/**
 * Resolve a preset value to the specialist-path payload, or null when the
 * selection is "Not specified" (or unknown, which cannot be sent by the UI).
 * A null result MUST be omitted from the payload — never serialized.
 */
export function specialistPathForPreset(presetValue: string): readonly string[] | null {
  const preset = SPECIALIST_PATH_PRESETS.find(
    (option) => option.value === presetValue,
  );
  return preset?.path ?? null;
}

/**
 * Coordinator-intent expectation options, mirroring the backend `AgentIntent`
 * literal exactly. The empty value is "no expectation" and is omitted from the
 * payload so cases stay backward compatible with runs that do not score intent.
 */
export const AGENT_INTENT_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "Not specified" },
  { value: "information", label: "Information" },
  { value: "action", label: "Action" },
  { value: "mixed", label: "Mixed" },
  { value: "none", label: "None" },
];

/** Mirrors `EvaluationRunQueuedResponse`. */
export interface EvaluationRunQueued {
  run_id: string;
  target_type: string;
  status: string;
  model: string;
  job_id: number;
  job_status: string;
}

async function postJson(
  path: string,
  body: unknown,
  fetcher: typeof fetch,
): Promise<Response> {
  return fetcher(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
}

/**
 * POST /api/backend/evaluations/runs/rag
 * Queues a RAG evaluation run. Safe, typed payload only; throws a generic
 * "Could not start evaluation." on any failure so raw backend errors never
 * reach the user.
 */
export async function startRagEvaluation(
  cases: EvalRAGCaseInput[],
  fetcher: typeof fetch = fetch,
): Promise<EvaluationRunQueued> {
  const response = await postJson(
    `${EVALUATIONS_RUNS_PATH}/rag`,
    { cases },
    fetcher,
  );
  if (!response.ok) {
    throw new Error(EVALUATION_START_ERROR);
  }
  return (await response.json()) as EvaluationRunQueued;
}

/**
 * POST /api/backend/evaluations/runs/agent
 * Queues an Agent evaluation run. Safe, typed payload only; throws a generic
 * "Could not start evaluation." on any failure so raw backend errors never
 * reach the user.
 */
export async function startAgentEvaluation(
  cases: EvalAgentCaseInput[],
  fetcher: typeof fetch = fetch,
): Promise<EvaluationRunQueued> {
  const response = await postJson(
    `${EVALUATIONS_RUNS_PATH}/agent`,
    { cases },
    fetcher,
  );
  if (!response.ok) {
    throw new Error(EVALUATION_START_ERROR);
  }
  return (await response.json()) as EvaluationRunQueued;
}

export interface QueuedEvaluationOutcome {
  queued: EvaluationRunQueued;
  runs: EvaluationRunListResponse;
}

/**
 * Queue a RAG evaluation, then fetch the refreshed run list so the page can
 * show the newly queued run at once. The GET happens only after a successful
 * POST.
 */
export async function queueRagEvaluation(
  cases: EvalRAGCaseInput[],
  fetcher: typeof fetch = fetch,
): Promise<QueuedEvaluationOutcome> {
  const queued = await startRagEvaluation(cases, fetcher);
  const runs = await fetchEvaluationRuns({}, fetcher);
  return { queued, runs };
}

/**
 * Queue an Agent evaluation, then fetch the refreshed run list so the page can
 * show the newly queued run at once. The GET happens only after a successful
 * POST.
 */
export async function queueAgentEvaluation(
  cases: EvalAgentCaseInput[],
  fetcher: typeof fetch = fetch,
): Promise<QueuedEvaluationOutcome> {
  const queued = await startAgentEvaluation(cases, fetcher);
  const runs = await fetchEvaluationRuns({}, fetcher);
  return { queued, runs };
}

export const EVALUATIONS_RELEASE_DECISIONS_PATH =
  "/api/backend/evaluations/release-decisions";

/** Max length the backend accepts for a release-decision note. */
export const EVALUATION_DECISION_NOTE_MAX = 1000;

/** Button label shown while a release decision request is in flight. */
export const EVALUATION_DECISION_SAVING_LABEL = "Saving...";

/** Success message shown after a decision is saved. */
export const EVALUATION_DECISION_APPROVED_MESSAGE = "Approved.";
/** Success message shown after a decision is saved. */
export const EVALUATION_DECISION_REJECTED_MESSAGE = "Rejected.";

/** Safe error label; raw backend errors never reach the user. */
export const EVALUATION_DECISION_ERROR = "Could not save decision.";

/** Safe message shown when an approved decision is blocked by a critical regression. */
export const EVALUATION_DECISION_BLOCKED_MESSAGE =
  "Approval blocked because a critical safety metric regressed.";

/**
 * Thrown when the backend blocks recording an approved decision with HTTP 409
 * (a critical safety metric regressed). Carries only the safe frontend copy
 * above — never the backend's detail string — so callers can tell this case
 * apart from a generic failure with `instanceof`.
 */
export class ReleaseDecisionBlockedError extends Error {
  constructor() {
    super(EVALUATION_DECISION_BLOCKED_MESSAGE);
    this.name = "ReleaseDecisionBlockedError";
  }
}

/** Safe error label when the release-decision history cannot be loaded. */
export const EVALUATION_DECISIONS_LOAD_ERROR = "Could not load release decisions.";

/** A release decision a human records against a candidate run. */
export type EvaluationReleaseDecision = "approved" | "rejected";

/**
 * Mirrors `EvaluationReleaseDecisionRead`. The decision record carries only
 * the safe stored fields; the run's `input` snapshot and any raw payload are
 * never part of this contract.
 */
export interface EvaluationReleaseDecisionRecord {
  id: number;
  organization_id: number;
  candidate_run_id: number;
  baseline_id: number;
  decision: string;
  decided_by_subject: string;
  note: string | null;
  comparison_snapshot: Record<string, unknown>;
  created_at: string;
}

/** Mirrors `EvaluationReleaseDecisionListResponse`. */
export interface EvaluationReleaseDecisionListResponse {
  items: EvaluationReleaseDecisionRecord[];
  total: number;
  limit: number;
  offset: number;
}

/** The only payload keys the frontend may send when recording a decision. */
export interface EvaluationReleaseDecisionInput {
  baseline_id: number;
  decision: EvaluationReleaseDecision;
  note?: string;
}

/**
 * POST /api/backend/evaluations/runs/{run_id}/release-decision
 * Records an approved/rejected decision against a succeeded run. The payload
 * contains only the baseline id, the decision, and an optional human note —
 * never `organization_id`, `decided_by_subject`, or `comparison_snapshot`.
 * Those are derived from the trusted auth context on the backend.
 */
export async function recordReleaseDecision(
  runId: string,
  input: EvaluationReleaseDecisionInput,
  fetcher: typeof fetch = fetch,
): Promise<EvaluationReleaseDecisionRecord> {
  const response = await postJson(
    `${EVALUATIONS_RUNS_PATH}/${encodeURIComponent(runId)}/release-decision`,
    input,
    fetcher,
  );
  if (response.status === 409) {
    throw new ReleaseDecisionBlockedError();
  }
  if (!response.ok) {
    throw new Error(EVALUATION_DECISION_ERROR);
  }
  return (await response.json()) as EvaluationReleaseDecisionRecord;
}

export interface EvaluationReleaseDecisionQuery {
  limit?: number;
  offset?: number;
}

/**
 * GET /api/backend/evaluations/release-decisions?limit=50&offset=0
 * Tenant-scoped history, newest first. Bounded to the same default and max
 * limit the backend clamps to.
 */
export async function fetchReleaseDecisions(
  query: EvaluationReleaseDecisionQuery = {},
  fetcher: typeof fetch = fetch,
): Promise<EvaluationReleaseDecisionListResponse> {
  const limit = query.limit ?? EVALUATIONS_DEFAULT_LIMIT;
  const offset = query.offset ?? EVALUATIONS_DEFAULT_OFFSET;
  return readJson<EvaluationReleaseDecisionListResponse>(
    await fetcher(
      `${EVALUATIONS_RELEASE_DECISIONS_PATH}?limit=${limit}&offset=${offset}`,
      { cache: "no-store" },
    ),
    EVALUATION_DECISIONS_LOAD_ERROR,
  );
}

export interface ReviewReleaseDecisionOutcome {
  decision: EvaluationReleaseDecisionRecord;
  history: EvaluationReleaseDecisionListResponse;
}

/**
 * Record a decision, then fetch the refreshed decision history so the table
 * shows the new decision at once. The GET happens only after a successful POST.
 */
export async function reviewReleaseDecision(
  run: EvaluationRun,
  input: EvaluationReleaseDecisionInput,
  fetcher: typeof fetch = fetch,
): Promise<ReviewReleaseDecisionOutcome> {
  const decision = await recordReleaseDecision(run.run_id, input, fetcher);
  const history = await fetchReleaseDecisions({}, fetcher);
  return { decision, history };
}

/** The state that decides whether Approve/Reject actions are visible. */
export interface ReleaseDecisionVisibility {
  can: (capability: Capability) => boolean;
  run: EvaluationRun | null | undefined;
  hasSelectedBaseline: boolean;
  hasComparison: boolean;
}

/**
 * Approve/Reject actions appear only when every gate passes: the subject has
 * `evaluation.manage`, a run is selected and has succeeded, a baseline is
 * selected, and the comparison has loaded successfully. No role-name checks;
 * the backend stays authoritative.
 */
export function canShowReleaseDecisionActions({
  can,
  run,
  hasSelectedBaseline,
  hasComparison,
}: ReleaseDecisionVisibility): boolean {
  return (
    can(CAPABILITIES.EVALUATION_MANAGE) &&
    run !== null &&
    run !== undefined &&
    run.status === "succeeded" &&
    hasSelectedBaseline &&
    hasComparison
  );
}

/** Display label for a decision: "Approved" / "Rejected", verbatim otherwise. */
export function decisionLabel(decision: string): string {
  if (decision === "approved") return "Approved";
  if (decision === "rejected") return "Rejected";
  return decision;
}

/** The exact display contract for one release-decision history row. */
export interface EvaluationReleaseDecisionRow {
  decision: string;
  decisionLabel: string;
  candidateRunId: number;
  baselineId: number;
  reviewer: string;
  note: string | null;
  createdLabel: string;
}

/**
 * Project a decision record into display rows. Only safe audit fields are
 * exposed: decision, decider, baseline/candidate references, human note, and a
 * formatted timestamp. The `comparison_snapshot` contents are never rendered.
 */
export function releaseDecisionRow(
  record: EvaluationReleaseDecisionRecord,
): EvaluationReleaseDecisionRow {
  return {
    decision: record.decision,
    decisionLabel: decisionLabel(record.decision),
    candidateRunId: record.candidate_run_id,
    baselineId: record.baseline_id,
    reviewer: record.decided_by_subject,
    note: record.note,
    createdLabel: formatTimestamp(record.created_at),
  };
}

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
};

const TARGET_LABELS: Record<string, string> = {
  rag: "RAG",
  agent: "Agent",
  repeatability: "Repeatability",
  latency: "Latency",
};

export function targetTypeLabel(targetType: string): string {
  return TARGET_LABELS[targetType] ?? targetType;
}

export function runStatusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/**
 * Render a decimal pass rate as a percentage, or "—" when no rate exists.
 * `0.85` → `85%`; `null`/`undefined` → `—`.
 */
export function formatPassRate(passRate: number | null | undefined): string {
  if (passRate === null || passRate === undefined) {
    return "—";
  }
  const percent = Math.round(passRate * 100);
  return `${percent}%`;
}

/** Render a UTC ISO timestamp as a short local date/time, or "—" when null. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Dimensions render only when they actually exist. RAG and Agent runs expose
 * different dimension keys, so the case table shows whichever the backend
 * supplied rather than guessing missing metrics.
 */
const RAG_DIMENSIONS = [
  { key: "retrieval_hit", label: "Retrieval" },
  { key: "answer_correct", label: "Answer" },
  { key: "grounding_correct", label: "Grounding" },
  { key: "citation_valid", label: "Citation" },
  { key: "refusal_correct", label: "Refusal" },
] as const;

const AGENT_DIMENSIONS = [
  { key: "action_pass", label: "Action" },
  { key: "retrieval_pass", label: "Retrieval" },
  { key: "tool_pass", label: "Tool" },
  { key: "auto_execute_pass", label: "Auto-execute" },
  { key: "intent_pass", label: "Intent" },
  { key: "specialist_path_pass", label: "Specialist path" },
  { key: "overall_pass", label: "Overall" },
] as const;

export interface EvaluationDimensionView {
  label: string;
  value: string;
}

/**
 * Case dimensions into a safe label/value list. Only dimensions that exist in
 * the response are shown; nothing sensitive is added by the frontend.
 */
export function dimensionsForCase(
  caseType: string,
  dimensions: Record<string, unknown>,
): EvaluationDimensionView[] {
  if (caseType === "agent") {
    return AGENT_DIMENSIONS.flatMap((dimension) =>
      dimensions[dimension.key] === undefined
        ? []
        : [{ label: dimension.label, value: String(dimensions[dimension.key]) }],
    );
  }
  return RAG_DIMENSIONS.flatMap((dimension) =>
    dimensions[dimension.key] === undefined
      ? []
      : [{ label: dimension.label, value: String(dimensions[dimension.key]) }],
  );
}

/**
 * Render a latency value in milliseconds as a compact human label, or "—".
 */
export function formatLatency(latencyMs: number | null | undefined): string {
  if (latencyMs === null || latencyMs === undefined) {
    return "—";
  }
  return `${Math.round(latencyMs)}ms`;
}

/**
 * Short selector label for one baseline, e.g. `RAG · v2 · 85%`. Uses only
 * fields the backend returns: target type, version, pass rate.
 */
export function baselineSelectLabel(baseline: EvaluationBaseline): string {
  return `${targetTypeLabel(baseline.target_type)} · ${baseline.version} · ${formatPassRate(
    baseline.pass_rate,
  )}`;
}

/**
 * Pass-rate delta as an explicitly signed percentage:
 * `+10%`, `-5%`, `0%`, or "—" when no delta exists.
 */
export function formatPassRateDelta(
  delta: number | null | undefined,
): string {
  if (delta === null || delta === undefined) {
    return "—";
  }
  const percent = Math.round(delta * 100);
  if (percent > 0) {
    return `+${percent}%`;
  }
  if (percent < 0) {
    return `${percent}%`;
  }
  return "0%";
}

/**
 * Compact value label for one compared metric (a float on the same scale the
 * backend uses, e.g. `0.85`, `1`). No unit guessing happens here.
 */
export function formatMetricValue(value: number): string {
  return String(Math.round(value * 100) / 100);
}

/** Explicitly signed, compact delta label for one compared metric. */
export function formatMetricDelta(delta: number): string {
  const rounded = Math.round(delta * 100) / 100;
  if (rounded > 0) {
    return `+${String(rounded)}`;
  }
  if (rounded < 0) {
    return String(rounded);
  }
  return "0";
}

const DIRECTION_LABELS: Record<string, string> = {
  improved: "Improved",
  regressed: "Regressed",
  same: "Same",
};

/** Render the backend direction verbatim, mapped to a display label. */
export function directionLabel(direction: string): string {
  return DIRECTION_LABELS[direction] ?? direction;
}

/**
 * High-level pass-rate summary of a comparison. Renders only pass rates and
 * the backend delta; no recommendation or verdict is derived by the frontend.
 */
export interface EvaluationComparisonSummary {
  baselinePassRate: string;
  candidatePassRate: string;
  deltaLabel: string;
}

export function comparisonSummary(
  comparison: EvaluationRunComparison,
): EvaluationComparisonSummary {
  return {
    baselinePassRate: formatPassRate(comparison.baseline_pass_rate),
    candidatePassRate: formatPassRate(comparison.candidate_pass_rate),
    deltaLabel: formatPassRateDelta(comparison.pass_rate_delta),
  };
}

/** One compared metric as a safe, display-only row. */
export interface EvaluationMetricRow {
  metric: string;
  baselineLabel: string;
  candidateLabel: string;
  deltaLabel: string;
  direction: string;
  directionLabel: string;
}

/**
 * Compared metrics into display rows. Only metrics the backend actually
 * compared (present on both sides) are shown; nothing is invented.
 */
export function metricComparisonRows(
  metrics: readonly EvaluationMetricComparison[],
): EvaluationMetricRow[] {
  return metrics.map((comparison) => ({
    metric: comparison.metric,
    baselineLabel: formatMetricValue(comparison.baseline),
    candidateLabel: formatMetricValue(comparison.candidate),
    deltaLabel: formatMetricDelta(comparison.delta),
    direction: comparison.direction,
    directionLabel: directionLabel(comparison.direction),
  }));
}

/** One version-identity field as a display-only row. */
export interface EvaluationIdentityRow {
  label: string;
  value: string;
}

/**
 * Version identity into display rows. Only fields actually present in the
 * snapshot are rendered; the corpus revision is shown as `present` without
 * exposing its contents, and nothing here judges the values.
 */
export function identityRows(
  snapshot: EvaluationIdentitySnapshot | null | undefined,
): EvaluationIdentityRow[] {
  if (!snapshot) {
    return [];
  }
  const rows: EvaluationIdentityRow[] = [{ label: "Model", value: snapshot.model }];
  if (snapshot.embedding_model !== null && snapshot.embedding_model !== undefined) {
    rows.push({ label: "Embedding model", value: snapshot.embedding_model });
  }
  if (
    snapshot.agent_decision_version !== null &&
    snapshot.agent_decision_version !== undefined
  ) {
    rows.push({
      label: "Agent decision version",
      value: snapshot.agent_decision_version,
    });
  }
  if (
    snapshot.tool_policy_version !== null &&
    snapshot.tool_policy_version !== undefined
  ) {
    rows.push({
      label: "Tool policy version",
      value: String(snapshot.tool_policy_version),
    });
  }
  if (snapshot.corpus_revision) {
    rows.push({ label: "Corpus revision", value: "present" });
  }
  return rows;
}

/**
 * The exact display contract for a run-table row. Only safe, derived fields
 * are projected here; run input snapshots and any other raw payload are never
 * part of the typed UI contract, so they cannot be rendered.
 */
export interface EvaluationRunDisplayRow {
  target: string;
  statusLabel: string;
  passRate: string;
  model: string;
  createdLabel: string;
  completedLabel: string;
}

export function runDisplayRow(run: EvaluationRun): EvaluationRunDisplayRow {
  return {
    target: targetTypeLabel(run.target_type),
    statusLabel: runStatusLabel(run.status),
    passRate: formatPassRate(run.pass_rate),
    model: run.model,
    createdLabel: formatTimestamp(run.created_at),
    completedLabel: formatTimestamp(run.completed_at),
  };
}

/**
 * The exact display contract for a case-result row. Shows only the case id,
 * type, dimensions that actually exist, and latency — nothing sensitive.
 */
export interface EvaluationCaseDisplayRow {
  caseId: string;
  caseType: string;
  dimensions: EvaluationDimensionView[];
  latencyLabel: string;
}

export function caseDisplayRow(
  caseResult: EvaluationCaseResult,
): EvaluationCaseDisplayRow {
  return {
    caseId: caseResult.case_id,
    caseType: caseResult.case_type,
    dimensions: dimensionsForCase(caseResult.case_type, caseResult.dimensions),
    latencyLabel: formatLatency(caseResult.latency_ms),
  };
}