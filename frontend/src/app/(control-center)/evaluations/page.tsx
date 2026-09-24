"use client";

import {
  CheckCircle2,
  ClipboardCheck,
  LoaderCircle,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAuthorization } from "@/lib/authorization/context";
import {
  EVALUATION_BASELINES_ERROR,
  EVALUATION_COMPARE_ERROR,
  EVALUATION_DECISION_APPROVED_MESSAGE,
  EVALUATION_DECISION_BLOCKED_MESSAGE,
  EVALUATION_DECISION_ERROR,
  EVALUATION_DECISION_NOTE_MAX,
  EVALUATION_DECISION_REJECTED_MESSAGE,
  EVALUATION_DECISION_SAVING_LABEL,
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
  directionLabel,
  fetchEvaluationBaselines as fetchBaselines,
  fetchEvaluationRunCases as fetchCases,
  fetchEvaluationRuns as fetchRuns,
  fetchReleaseDecisions as fetchDecisions,
  formatPassRate,
  formatTimestamp,
  identityRows,
  isKnownEvaluationTarget,
  metricComparisonRows,
  promoteRunToBaseline,
  queueAgentEvaluation,
  queueRagEvaluation,
  ReleaseDecisionBlockedError,
  releaseDecisionRow,
  reviewReleaseDecision,
  runDisplayRow,
  runStatusLabel,
  targetTypeLabel,
  type EvaluationReleaseDecision,
  type EvaluationRun,
} from "@/lib/evaluations";
import type {
  EvaluationBaseline,
  EvaluationCaseResult,
  EvaluationIdentityRow,
  EvaluationReleaseDecisionRecord,
  EvaluationRunComparison,
} from "@/lib/evaluations";

// NOTE on organization switch: the control-center layout keys the
// `AuthorizationProvider` by the active organization id, so switching
// organizations remounts this page subtree and resets its state. Old
// evaluation data from a previous organization can therefore never stay
// visible after a switch — no additional reset code is required here.

type BadgeVariant = "default" | "success" | "warning" | "danger" | "info" | "violet";

function Badge({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?: BadgeVariant;
}) {
  const styles: Record<BadgeVariant, string> = {
    default: "border-slate-200 bg-slate-50 text-slate-600",
    success: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning: "border-amber-200 bg-amber-50 text-amber-700",
    danger: "border-rose-200 bg-rose-50 text-rose-700",
    info: "border-blue-200 bg-blue-50 text-blue-700",
    violet: "border-violet-200 bg-violet-50 text-violet-700",
  };

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

function statusVariant(status: string): BadgeVariant {
  switch (status) {
    case "succeeded":
      return "success";
    case "failed":
      return "danger";
    case "running":
      return "info";
    case "queued":
    default:
      return "default";
  }
}

function dimensionVariant(value: string): BadgeVariant {
  if (value === "true") return "success";
  if (value === "false") return "danger";
  return "default";
}

function directionVariant(direction: string): BadgeVariant {
  if (direction === "improved") return "success";
  if (direction === "regressed") return "danger";
  return "default";
}

function decisionVariant(decision: string): BadgeVariant {
  if (decision === "approved") return "success";
  if (decision === "rejected") return "danger";
  return "default";
}

function IdentityList({ rows }: { rows: EvaluationIdentityRow[] }) {
  if (rows.length === 0) {
    return <p className="mt-2 text-sm text-slate-400">—</p>;
  }
  return (
    <dl className="mt-3 space-y-2">
      {rows.map((row) => (
        <div
          key={row.label}
          className="flex flex-wrap items-baseline justify-between gap-2"
        >
          <dt className="text-xs text-slate-400">{row.label}</dt>
          <dd className="break-all font-medium text-slate-900">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ComparisonPanel({
  comparison,
}: {
  comparison: EvaluationRunComparison;
}) {
  const summary = comparisonSummary(comparison);
  const metricRows = metricComparisonRows(comparison.metrics);

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-3">
        <div className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5">
          <p className="text-xs text-slate-400">Baseline pass rate</p>
          <p className="mt-2 text-xl font-medium text-slate-900">
            {summary.baselinePassRate}
          </p>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
          <p className="text-xs text-slate-400">Candidate pass rate</p>
          <p className="mt-2 text-xl font-medium text-slate-900">
            {summary.candidatePassRate}
          </p>
        </div>
        <div className="rounded-2xl border border-violet-200/80 bg-violet-50/50 p-5">
          <p className="text-xs text-slate-400">Difference</p>
          <p className="mt-2 text-xl font-medium text-slate-900">
            {summary.deltaLabel}
          </p>
        </div>
      </div>

      {metricRows.length > 0 ? (
        <div className="overflow-hidden rounded-2xl border border-slate-200/80">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200/70 bg-slate-50/70 text-[10px] uppercase tracking-[0.12em] text-slate-400">
              <tr>
                <th className="px-5 py-3 font-semibold">Metric</th>
                <th className="px-5 py-3 font-semibold">Baseline</th>
                <th className="px-5 py-3 font-semibold">Candidate</th>
                <th className="px-5 py-3 font-semibold">Change</th>
                <th className="px-5 py-3 font-semibold">Direction</th>
              </tr>
            </thead>
            <tbody>
              {metricRows.map((row) => (
                <tr
                  key={row.metric}
                  className="border-b border-slate-200/60 last:border-b-0"
                >
                  <td className="px-5 py-4 font-mono text-xs text-slate-600">
                    {row.metric}
                  </td>
                  <td className="px-5 py-4 font-mono text-xs text-slate-600">
                    {row.baselineLabel}
                  </td>
                  <td className="px-5 py-4 font-mono text-xs text-slate-600">
                    {row.candidateLabel}
                  </td>
                  <td className="px-5 py-4 font-mono text-xs text-slate-600">
                    {row.deltaLabel}
                  </td>
                  <td className="px-5 py-4">
                    <Badge variant={directionVariant(row.direction)}>
                      {directionLabel(row.direction)}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 text-sm text-slate-400">
          No shared metrics between this run and the selected baseline.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
          <p className="text-xs text-slate-400">Baseline identity</p>
          <IdentityList rows={identityRows(comparison.baseline)} />
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
          <p className="text-xs text-slate-400">Candidate identity</p>
          <IdentityList rows={identityRows(comparison.candidate)} />
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-slate-400">
        {label}
      </span>
      {children}
    </label>
  );
}

function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className="h-10 rounded-[12px] border border-slate-200 bg-white px-3.5 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:ring-2 focus:ring-violet-100 disabled:opacity-50"
    />
  );
}

function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex items-center gap-2.5 text-sm text-slate-600">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 rounded border-slate-300 text-violet-600 accent-violet-600"
      />
      {label}
    </label>
  );
}

export default function EvaluationsPage() {
  const { can } = useAuthorization();
  const canManage = canManageEvaluations(can);

  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<EvaluationRun | null>(null);
  const [caseResults, setCaseResults] = useState<{
    runId: string;
    items: EvaluationCaseResult[];
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [startError, setStartError] = useState("");
  const [startMessage, setStartMessage] = useState("");
  const [starting, setStarting] = useState<"rag" | "agent" | null>(null);

  // Baseline comparison state. Read-only: baselines are fetched to populate
  // the selector and compared with a single GET; nothing is mutated here.
  const [baselines, setBaselines] = useState<EvaluationBaseline[]>([]);
  const [selectedBaselineId, setSelectedBaselineId] = useState<number | null>(
    null,
  );
  const [baselinesLoading, setBaselinesLoading] = useState(false);
  const [baselinesError, setBaselinesError] = useState("");
  const [comparison, setComparison] = useState<EvaluationRunComparison | null>(
    null,
  );
  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState("");

  // Promote-to-baseline state. Promotion is a single POST; the button is
  // disabled while a request is in flight so repeated clicks cannot send
  // duplicate POSTs.
  const [promoting, setPromoting] = useState(false);
  const [promoteError, setPromoteError] = useState("");
  const [promoteMessage, setPromoteMessage] = useState("");

  // Release-decision state. Approve/Reject share one in-flight flag so a
  // second decision request cannot start until the first finishes; the note is
  // an optional human audit text and never leaves the safe payload contract.
  const [decisionNote, setDecisionNote] = useState("");
  const [savingDecision, setSavingDecision] = useState(false);
  const [decisionMessage, setDecisionMessage] = useState("");
  const [decisionError, setDecisionError] = useState("");
  const [decisionBlocked, setDecisionBlocked] = useState(false);
  const [decisionHistory, setDecisionHistory] = useState<
    EvaluationReleaseDecisionRecord[]
  >([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // RAG form values (mirror EvalRAGCaseInput).
  const [ragId, setRagId] = useState("");
  const [ragQuestion, setRagQuestion] = useState("");
  const [ragSources, setRagSources] = useState("");
  const [ragTerms, setRagTerms] = useState("");
  const [ragShouldRefuse, setRagShouldRefuse] = useState(false);

  // Agent form values (mirror EvalAgentCaseInput).
  const [agentTicketId, setAgentTicketId] = useState("");
  const [agentAction, setAgentAction] = useState("");
  const [agentExpectedRetrieval, setAgentExpectedRetrieval] = useState(false);
  const [agentExpectedTool, setAgentExpectedTool] = useState("");
  const [agentExpectedAutoExecute, setAgentExpectedAutoExecute] = useState(false);
  const [agentFingerprint, setAgentFingerprint] = useState("");

  const submitRag = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canManage) {
      return;
    }
    setStartError("");
    setStartMessage("");
    setStarting("rag");
    try {
      const outcome = await queueRagEvaluation([
        {
          id: ragId,
          question: ragQuestion,
          expected_sources: ragSources
            .split(",")
            .map((part) => part.trim())
            .filter(Boolean),
          expected_terms: ragTerms
            .split(",")
            .map((part) => part.trim())
            .filter(Boolean),
          should_refuse: ragShouldRefuse,
        },
      ]);
      setRuns(outcome.runs.items);
      setSelectedRun((current) =>
        current &&
        outcome.runs.items.some((run) => run.run_id === current.run_id)
          ? current
          : (outcome.runs.items[0] ?? null),
      );
      setStartMessage(EVALUATION_QUEUED_MESSAGE);
      setRagId("");
      setRagQuestion("");
      setRagSources("");
      setRagTerms("");
      setRagShouldRefuse(false);
    } catch (err) {
      setStartError(
        err instanceof Error ? err.message : EVALUATION_START_ERROR,
      );
    } finally {
      setStarting(null);
    }
  };

  const submitAgent = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canManage) {
      return;
    }
    setStartError("");
    setStartMessage("");
    setStarting("agent");
    try {
      const outcome = await queueAgentEvaluation([
        {
          ticket_id: Number(agentTicketId),
          expected_action: agentAction,
          expected_retrieval: agentExpectedRetrieval,
          expected_tool: agentExpectedTool,
          expected_auto_execute: agentExpectedAutoExecute,
          ...(agentFingerprint.trim() !== ""
            ? { fingerprint: agentFingerprint.trim() }
            : {}),
        },
      ]);
      setRuns(outcome.runs.items);
      setSelectedRun((current) =>
        current &&
        outcome.runs.items.some((run) => run.run_id === current.run_id)
          ? current
          : (outcome.runs.items[0] ?? null),
      );
      setStartMessage(EVALUATION_QUEUED_MESSAGE);
      setAgentTicketId("");
      setAgentAction("");
      setAgentExpectedRetrieval(false);
      setAgentExpectedTool("");
      setAgentExpectedAutoExecute(false);
      setAgentFingerprint("");
    } catch (err) {
      setStartError(
        err instanceof Error ? err.message : EVALUATION_START_ERROR,
      );
    } finally {
      setStarting(null);
    }
  };

  const loadRuns = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetchRuns();
      setRuns(response.items);
      setSelectedRun((current) => {
        if (
          current &&
          response.items.some((run) => run.run_id === current.run_id)
        ) {
          return current;
        }
        return response.items[0] ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load evaluations.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCases = useCallback(async (runId: string) => {
    setError("");
    try {
      const response = await fetchCases(runId);
      setCaseResults({ runId, items: response.items });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load case results.");
      setCaseResults({ runId, items: [] });
    }
  }, []);

  // Baselines are fetched for the selected run's target type so the selector
  // only offers comparisons the backend will accept. Contents are keyed to the
  // run so a stale comparison never renders for a newly selected run.
  const loadBaselines = useCallback(async (targetType: string) => {
    setBaselinesLoading(true);
    setBaselinesError("");
    setComparison(null);
    setCompareError("");
    try {
      const response = await fetchBaselines({ targetType });
      setBaselines(response.items);
      setSelectedBaselineId((current) => {
        if (
          current !== null &&
          response.items.some((baseline) => baseline.id === current)
        ) {
          return current;
        }
        return response.items[0]?.id ?? null;
      });
    } catch (err) {
      setBaselinesError(
        err instanceof Error ? err.message : EVALUATION_BASELINES_ERROR,
      );
      setBaselines([]);
      setSelectedBaselineId(null);
    } finally {
      setBaselinesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!canViewEvaluations(can)) {
      return;
    }
    const timer = window.setTimeout(() => {
      void loadRuns();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [can, loadRuns]);

  // When the selected run changes, fetch its cases. A run with no cases yet
  // (still queued/running) simply shows an empty case table. Results are keyed
  // by run id so stale cases never render for a newly selected run.
  useEffect(() => {
    if (!selectedRun) {
      return;
    }
    const timer = window.setTimeout(() => {
      void loadCases(selectedRun.run_id);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [selectedRun, loadCases]);

  // When the selected run changes, fetch the matching baselines. An unknown
  // target type is never sent (the backend literal would 422); the section
  // simply shows the "no baselines" state without fetching.
  useEffect(() => {
    if (!selectedRun || !canViewEvaluations(can)) {
      return;
    }
    const timer = window.setTimeout(() => {
      if (!isKnownEvaluationTarget(selectedRun.target_type)) {
        setBaselines([]);
        setSelectedBaselineId(null);
        setBaselinesLoading(false);
        setComparison(null);
        setCompareError("");
        return;
      }
      void loadBaselines(selectedRun.target_type);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [selectedRun, can, loadBaselines]);

  // The release-decision history is tenant-scoped, so it loads once on page
  // open (EVALUATION_READ is a pre-condition for rendering the page). The
  // table is refreshed after every successful Approve/Reject.
  const loadDecisionHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const response = await fetchDecisions({ limit: 50, offset: 0 });
      setDecisionHistory(response.items);
    } catch {
      setDecisionHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!canViewEvaluations(can)) {
      return;
    }
    const timer = window.setTimeout(() => {
      void loadDecisionHistory();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [can, loadDecisionHistory]);

  const runCompare = async () => {
    if (!selectedRun || selectedBaselineId === null) {
      return;
    }
    setComparing(true);
    setCompareError("");
    setComparison(null);
    try {
      const result = await compareRunToBaseline(
        selectedRun.run_id,
        selectedBaselineId,
      );
      setComparison(result);
    } catch (err) {
      setCompareError(
        err instanceof Error ? err.message : EVALUATION_COMPARE_ERROR,
      );
    } finally {
      setComparing(false);
    }
  };

  // Promote the selected (succeeded) run to a baseline. The guard on
  // `promoting` plus the disabled button prevents duplicate POSTs from
  // repeated clicks; the backend remains authoritative on consistency.
  const submitPromote = async () => {
    if (!selectedRun || !canManage || promoting) {
      return;
    }
    if (!canPromoteRunToBaseline(selectedRun)) {
      return;
    }
    setPromoting(true);
    setPromoteError("");
    setPromoteMessage("");
    try {
      const outcome = await promoteRunToBaseline(selectedRun);
      // Keep the current run selected; update the baseline list and select the
      // newly created baseline.
      setBaselines(outcome.baselines.items);
      setSelectedBaselineId(outcome.baseline.id);
      setComparison(null);
      setCompareError("");
      setPromoteMessage(EVALUATION_PROMOTE_SUCCESS);
    } catch (err) {
      setPromoteError(
        err instanceof Error ? err.message : EVALUATION_PROMOTE_ERROR,
      );
    } finally {
      setPromoting(false);
    }
  };

  const cases =
    caseResults && selectedRun && caseResults.runId === selectedRun.run_id
      ? caseResults.items
      : [];

  // Record an approved/rejected decision. The client sends only baseline_id,
  // decision, and an optional note; organization and subject come from the
  // trusted auth context. The `savingDecision` guard plus the disabled buttons
  // prevent a second request while the first is still in flight. The selected
  // run, baseline, and comparison stay visible; only the history is refreshed.
  const submitDecision = async (decision: EvaluationReleaseDecision) => {
    if (
      !selectedRun ||
      selectedBaselineId === null ||
      comparison === null ||
      !canManage ||
      savingDecision
    ) {
      return;
    }
    setSavingDecision(true);
    setDecisionError("");
    setDecisionMessage("");
    setDecisionBlocked(false);
    try {
      const outcome = await reviewReleaseDecision(selectedRun, {
        baseline_id: selectedBaselineId,
        decision,
        ...(decisionNote.trim() !== "" ? { note: decisionNote.trim() } : {}),
      });
      setDecisionHistory(outcome.history.items);
      setDecisionMessage(
        decision === "approved"
          ? EVALUATION_DECISION_APPROVED_MESSAGE
          : EVALUATION_DECISION_REJECTED_MESSAGE,
      );
      setDecisionNote("");
    } catch (err) {
      if (err instanceof ReleaseDecisionBlockedError) {
        setDecisionBlocked(true);
      } else {
        setDecisionError(EVALUATION_DECISION_ERROR);
      }
    } finally {
      setSavingDecision(false);
    }
  };

  const canDecide = canShowReleaseDecisionActions({
    can,
    run: selectedRun,
    hasSelectedBaseline: selectedBaselineId !== null,
    hasComparison: comparison !== null,
  });

  if (!canViewEvaluations(can)) {
    return null;
  }

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Evaluations
              </p>
              <p className="hidden text-[11px] text-slate-400 sm:block">
                Read-only AI evaluation runs
              </p>
            </div>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => void loadRuns()}
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              AI Evaluation Results
            </p>
            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Evaluations
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Inspect persisted AI evaluation runs and their case-level
              dimensions across RAG and Agent targets.
            </p>
          </section>

          {error && (
            <div className="mb-6 flex gap-3 rounded-[18px] border border-rose-200 bg-rose-50/80 p-4 text-sm text-rose-700">
              <TriangleAlert className="h-5 w-5 shrink-0" />
              {error}
            </div>
          )}

          {canManage && (
            <section className="app-panel mb-6 rounded-[22px]">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200/70 p-5">
                <div>
                  <h2 className="font-medium text-slate-900">
                    Run a new evaluation
                  </h2>
                  <p className="mt-1 text-xs text-slate-400">
                    Queue a one-case RAG or Agent evaluation run.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="violet">evaluation.manage</Badge>
                </div>
              </div>

              {startError && (
                <div className="flex gap-3 border-b border-rose-200 bg-rose-50/80 px-5 py-3.5 text-sm text-rose-700">
                  <TriangleAlert className="h-5 w-5 shrink-0" />
                  {EVALUATION_START_ERROR}
                </div>
              )}
              {startMessage && (
                <div className="flex gap-3 border-b border-emerald-200 bg-emerald-50/80 px-5 py-3.5 text-sm text-emerald-700">
                  <CheckCircle2 className="h-5 w-5 shrink-0" />
                  {startMessage}
                </div>
              )}

              <div className="grid gap-6 p-5 lg:grid-cols-2">
                <form onSubmit={(event) => void submitRag(event)} className="space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-slate-900">
                      Run RAG Evaluation
                    </h3>
                    <Badge variant="info">rag</Badge>
                  </div>
                  <Field label="Case ID">
                    <TextInput
                      value={ragId}
                      onChange={(event) => setRagId(event.target.value)}
                      placeholder="rag-001"
                      required
                      maxLength={100}
                    />
                  </Field>
                  <Field label="Question">
                    <TextInput
                      value={ragQuestion}
                      onChange={(event) => setRagQuestion(event.target.value)}
                      placeholder="What refund options are available?"
                      required
                      maxLength={2000}
                    />
                  </Field>
                  <Field label="Expected sources (comma separated)">
                    <TextInput
                      value={ragSources}
                      onChange={(event) => setRagSources(event.target.value)}
                      placeholder="KB-101, KB-204"
                    />
                  </Field>
                  <Field label="Expected terms (comma separated)">
                    <TextInput
                      value={ragTerms}
                      onChange={(event) => setRagTerms(event.target.value)}
                      placeholder="refund, 30 days"
                    />
                  </Field>
                  <Checkbox
                    checked={ragShouldRefuse}
                    onChange={setRagShouldRefuse}
                    label="Should refuse"
                  />
                  <button
                    type="submit"
                    disabled={starting !== null}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-[12px] bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
                  >
                    {starting === "rag" && (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    )}
                    Run RAG Evaluation
                  </button>
                </form>

                <form onSubmit={(event) => void submitAgent(event)} className="space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-slate-900">
                      Run Agent Evaluation
                    </h3>
                    <Badge variant="info">agent</Badge>
                  </div>
                  <Field label="Ticket ID">
                    <TextInput
                      type="number"
                      min={1}
                      step={1}
                      value={agentTicketId}
                      onChange={(event) => setAgentTicketId(event.target.value)}
                      placeholder="4201"
                      required
                    />
                  </Field>
                  <Field label="Expected action">
                    <TextInput
                      value={agentAction}
                      onChange={(event) => setAgentAction(event.target.value)}
                      placeholder="refund"
                      required
                      maxLength={50}
                    />
                  </Field>
                  <Field label="Expected tool">
                    <TextInput
                      value={agentExpectedTool}
                      onChange={(event) => setAgentExpectedTool(event.target.value)}
                      placeholder="process_refund"
                      required
                      maxLength={100}
                    />
                  </Field>
                  <Checkbox
                    checked={agentExpectedRetrieval}
                    onChange={setAgentExpectedRetrieval}
                    label="Expected retrieval"
                  />
                  <Checkbox
                    checked={agentExpectedAutoExecute}
                    onChange={setAgentExpectedAutoExecute}
                    label="Expected auto-execute"
                  />
                  <Field label="Fingerprint (optional)">
                    <TextInput
                      value={agentFingerprint}
                      onChange={(event) => setAgentFingerprint(event.target.value)}
                      placeholder="agent-case-001"
                      maxLength={64}
                    />
                  </Field>
                  <button
                    type="submit"
                    disabled={starting !== null}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-[12px] bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
                  >
                    {starting === "agent" && (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    )}
                    Run Agent Evaluation
                  </button>
                </form>
              </div>
            </section>
          )}

          <div className="grid gap-6 xl:grid-cols-[410px_minmax(0,1fr)]">
            <section className="app-panel self-start overflow-hidden rounded-[22px] xl:sticky xl:top-[96px]">
              <div className="flex items-center justify-between border-b border-slate-200/70 p-5">
                <div>
                  <h2 className="font-medium text-slate-900">Run list</h2>
                  <p className="mt-1 text-xs text-slate-400">
                    {loading && runs.length === 0
                      ? "Loading…"
                      : `${runs.length} run${runs.length === 1 ? "" : "s"}`}
                  </p>
                </div>
                <Badge variant="violet">{runs.length}</Badge>
              </div>

              <div
                data-lenis-prevent
                className="max-h-[760px] overflow-y-auto overscroll-contain"
              >
                {loading && runs.length === 0 ? (
                  <div className="flex h-48 items-center justify-center">
                    <LoaderCircle className="h-6 w-6 animate-spin text-violet-500" />
                  </div>
                ) : runs.length === 0 ? (
                  <div className="p-10 text-center">
                    <ClipboardCheck className="mx-auto h-7 w-7 text-slate-300" />
                    <p className="mt-3 text-sm text-slate-400">
                      No evaluation runs yet.
                    </p>
                  </div>
                ) : (
                  <table className="w-full text-left text-sm">
                    <thead className="border-b border-slate-200/70 bg-slate-50/70 text-[10px] uppercase tracking-[0.12em] text-slate-400">
                      <tr>
                        <th className="px-5 py-3 font-semibold">Target</th>
                        <th className="px-5 py-3 font-semibold">Status</th>
                        <th className="px-5 py-3 font-semibold">Pass rate</th>
                        <th className="hidden px-5 py-3 font-semibold md:table-cell">
                          Model
                        </th>
                        <th className="hidden px-5 py-3 font-semibold lg:table-cell">
                          Created
                        </th>
                        <th className="hidden px-5 py-3 font-semibold xl:table-cell">
                          Completed
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.map((run) => {
                        const selected = selectedRun?.run_id === run.run_id;
                        const row = runDisplayRow(run);
                        return (
                          <tr
                            key={run.run_id}
                            onClick={() => setSelectedRun(run)}
                            className={`cursor-pointer border-b border-slate-200/60 transition last:border-b-0 ${
                              selected
                                ? "bg-gradient-to-r from-violet-50/90 via-blue-50/40 to-white"
                                : "bg-white/30 hover:bg-slate-50/80"
                            }`}
                          >
                            <td className="px-5 py-4 font-medium text-slate-800">
                              {row.target}
                            </td>
                            <td className="px-5 py-4">
                              <Badge variant={statusVariant(run.status)}>
                                {row.statusLabel}
                              </Badge>
                            </td>
                            <td className="px-5 py-4 text-slate-600">
                              {row.passRate}
                            </td>
                            <td className="hidden px-5 py-4 text-slate-600 md:table-cell">
                              {row.model}
                            </td>
                            <td className="hidden px-5 py-4 text-xs text-slate-500 lg:table-cell">
                              {row.createdLabel}
                            </td>
                            <td className="hidden px-5 py-4 text-xs text-slate-500 xl:table-cell">
                              {row.completedLabel}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </section>

            {!selectedRun ? (
              <section className="app-panel flex min-h-[600px] items-center justify-center rounded-[22px]">
                <div className="text-center">
                  <ClipboardCheck className="mx-auto h-8 w-8 text-slate-300" />
                  <p className="mt-4 font-medium text-slate-700">
                    Select an evaluation run
                  </p>
                </div>
              </section>
            ) : (
              <div className="space-y-6">
                <section className="app-panel overflow-hidden rounded-[22px]">
                  <div className="border-b border-slate-200/70 bg-gradient-to-r from-violet-50/70 via-white to-blue-50/55 p-6 md:p-7">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="flex flex-wrap gap-2">
                        <Badge variant={statusVariant(selectedRun.status)}>
                          {runStatusLabel(selectedRun.status)}
                        </Badge>
                        <Badge variant="violet">{selectedRun.target_type}</Badge>
                        <Badge variant="info">{selectedRun.trigger_source}</Badge>
                        {selectedRun.pass_rate !== null && (
                          <Badge variant="success">
                            Pass rate {formatPassRate(selectedRun.pass_rate)}
                          </Badge>
                        )}
                      </div>

                      {canManage && canPromoteRunToBaseline(selectedRun) && (
                        <button
                          type="button"
                          onClick={() => void submitPromote()}
                          disabled={promoting}
                          className="inline-flex h-10 items-center justify-center gap-2 rounded-[12px] bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
                        >
                          {promoting && (
                            <LoaderCircle className="h-4 w-4 animate-spin" />
                          )}
                          {promoting
                            ? EVALUATION_PROMOTING_LABEL
                            : "Promote to Baseline"}
                        </button>
                      )}
                    </div>

                    <h2 className="mt-5 text-2xl font-medium tracking-[-0.035em] text-slate-950">
                      {targetTypeLabel(selectedRun.target_type)} run
                    </h2>
                    <p className="mt-2 break-all font-mono text-[10px] text-slate-400">
                      {selectedRun.run_id}
                    </p>

                    {promoting && (
                      <p className="mt-4 text-sm text-slate-500">
                        Creating a baseline from this run…
                      </p>
                    )}
                    {promoteMessage && (
                      <div className="mt-4 flex gap-3 rounded-[18px] border border-emerald-200 bg-emerald-50/80 p-4 text-sm text-emerald-700">
                        <CheckCircle2 className="h-5 w-5 shrink-0" />
                        {promoteMessage}
                      </div>
                    )}
                    {promoteError && (
                      <div className="mt-4 flex gap-3 rounded-[18px] border border-rose-200 bg-rose-50/80 p-4 text-sm text-rose-700">
                        <TriangleAlert className="h-5 w-5 shrink-0" />
                        {promoteError}
                      </div>
                    )}
                  </div>

                  <div className="p-6 md:p-7">
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                        <p className="text-xs text-slate-400">Model</p>
                        <p className="mt-2 font-medium text-slate-900">
                          {selectedRun.model}
                        </p>
                      </div>
                      <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                        <p className="text-xs text-slate-400">Pass rate</p>
                        <p className="mt-2 font-medium text-slate-900">
                          {formatPassRate(selectedRun.pass_rate)}
                        </p>
                      </div>
                      <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                        <p className="text-xs text-slate-400">Embedding model</p>
                        <p className="mt-2 font-medium text-slate-900">
                          {selectedRun.embedding_model ?? "—"}
                        </p>
                      </div>
                      <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                        <p className="text-xs text-slate-400">
                          Agent decision version
                        </p>
                        <p className="mt-2 font-medium text-slate-900">
                          {selectedRun.agent_decision_version ?? "—"}
                        </p>
                      </div>
                      <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                        <p className="text-xs text-slate-400">Created</p>
                        <p className="mt-2 font-medium text-slate-900">
                          {formatTimestamp(selectedRun.created_at)}
                        </p>
                      </div>
                      <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                        <p className="text-xs text-slate-400">Started</p>
                        <p className="mt-2 font-medium text-slate-900">
                          {formatTimestamp(selectedRun.started_at)}
                        </p>
                      </div>
                      <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                        <p className="text-xs text-slate-400">Completed</p>
                        <p className="mt-2 font-medium text-slate-900">
                          {formatTimestamp(selectedRun.completed_at)}
                        </p>
                      </div>
                      {selectedRun.error && (
                        <div className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5 md:col-span-2">
                          <p className="text-xs text-slate-400">Error</p>
                          <p className="mt-2 text-sm text-rose-600">
                            {selectedRun.error}
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                </section>

                <section className="app-panel overflow-hidden rounded-[22px]">
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200/70 p-6">
                    <div>
                      <h2 className="font-medium text-slate-900">
                        Compare with baseline
                      </h2>
                      <p className="mt-1 text-xs text-slate-400">
                        Read-only comparison of this run against a baseline for
                        the same target type.
                      </p>
                    </div>
                    <Badge variant="violet">baselines</Badge>
                  </div>

                  {baselinesError ? (
                    <div className="flex gap-3 p-6 text-sm text-rose-700">
                      <TriangleAlert className="h-5 w-5 shrink-0" />
                      {EVALUATION_BASELINES_ERROR}
                    </div>
                  ) : baselinesLoading ? (
                    <div className="flex items-center gap-2 p-6 text-sm text-slate-400">
                      <LoaderCircle className="h-4 w-4 animate-spin text-violet-500" />
                      Loading baselines...
                    </div>
                  ) : baselines.length === 0 ? (
                    <div className="p-6 text-sm text-slate-400">
                      No baselines available for this evaluation type.
                    </div>
                  ) : (
                    <div className="space-y-5 p-6">
                      <div className="flex flex-wrap items-end gap-3">
                        <Field label="Baseline">
                          <select
                            value={selectedBaselineId ?? ""}
                            onChange={(event) => {
                              setSelectedBaselineId(Number(event.target.value));
                              setComparison(null);
                              setCompareError("");
                            }}
                            className="h-10 min-w-[240px] rounded-[12px] border border-slate-200 bg-white px-3.5 text-sm text-slate-800 shadow-sm outline-none transition focus:border-violet-300 focus:ring-2 focus:ring-violet-100"
                          >
                            {baselines.map((baseline) => (
                              <option key={baseline.id} value={baseline.id}>
                                {baselineSelectLabel(baseline)}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <button
                          type="button"
                          onClick={() => void runCompare()}
                          disabled={comparing || selectedBaselineId === null}
                          className="inline-flex h-10 items-center justify-center gap-2 rounded-[12px] bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
                        >
                          {comparing && (
                            <LoaderCircle className="h-4 w-4 animate-spin" />
                          )}
                          {comparing ? "Comparing..." : "Compare"}
                        </button>
                      </div>

                      {compareError && (
                        <div className="flex gap-3 p-4 text-sm text-rose-700">
                          <TriangleAlert className="h-5 w-5 shrink-0" />
                          {EVALUATION_COMPARE_ERROR}
                        </div>
                      )}

                      {comparison && (
                        <ComparisonPanel comparison={comparison} />
                      )}

                      {canDecide && (
                        <div className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5">
                          <div className="flex flex-wrap items-end justify-between gap-3">
                            <Field label="Decision note (optional)">
                              <TextInput
                                value={decisionNote}
                                onChange={(event) =>
                                  setDecisionNote(
                                    event.target.value.slice(
                                      0,
                                      EVALUATION_DECISION_NOTE_MAX,
                                    ),
                                  )
                                }
                                placeholder="Short, human-readable reason"
                                maxLength={EVALUATION_DECISION_NOTE_MAX}
                                disabled={savingDecision}
                              />
                            </Field>
                            <div className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => void submitDecision("approved")}
                                disabled={savingDecision}
                                className="inline-flex h-10 items-center justify-center gap-2 rounded-[12px] bg-emerald-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-emerald-700 disabled:opacity-50"
                              >
                                {savingDecision && (
                                  <LoaderCircle className="h-4 w-4 animate-spin" />
                                )}
                                {savingDecision
                                  ? EVALUATION_DECISION_SAVING_LABEL
                                  : "Approve"}
                              </button>
                              <button
                                type="button"
                                onClick={() => void submitDecision("rejected")}
                                disabled={savingDecision}
                                className="inline-flex h-10 items-center justify-center gap-2 rounded-[12px] bg-rose-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-rose-700 disabled:opacity-50"
                              >
                                {savingDecision && (
                                  <LoaderCircle className="h-4 w-4 animate-spin" />
                                )}
                                {savingDecision
                                  ? EVALUATION_DECISION_SAVING_LABEL
                                  : "Reject"}
                              </button>
                            </div>
                          </div>

                          {decisionError && (
                            <div className="mt-4 flex gap-3 text-sm text-rose-700">
                              <TriangleAlert className="h-5 w-5 shrink-0" />
                              {EVALUATION_DECISION_ERROR}
                            </div>
                          )}
                          {decisionBlocked && (
                            <div className="mt-4 flex gap-3 text-sm text-amber-700">
                              <TriangleAlert className="h-5 w-5 shrink-0" />
                              {EVALUATION_DECISION_BLOCKED_MESSAGE}
                            </div>
                          )}
                          {decisionMessage && (
                            <div className="mt-4 flex gap-3 text-sm text-emerald-700">
                              <CheckCircle2 className="h-5 w-5 shrink-0" />
                              {decisionMessage}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </section>

                <section className="app-panel overflow-hidden rounded-[22px]">
                  <div className="flex items-center justify-between border-b border-slate-200/70 p-6">
                    <div>
                      <h2 className="font-medium text-slate-900">Case results</h2>
                      <p className="mt-1 text-xs text-slate-400">
                        {cases.length} case{cases.length === 1 ? "" : "s"}
                      </p>
                    </div>
                    <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                  </div>

                  {cases.length === 0 ? (
                    <div className="p-10 text-center text-sm text-slate-400">
                      No case results yet.
                    </div>
                  ) : (
                    <table className="w-full text-left text-sm">
                      <thead className="border-b border-slate-200/70 bg-slate-50/70 text-[10px] uppercase tracking-[0.12em] text-slate-400">
                        <tr>
                          <th className="px-6 py-3 font-semibold">Case</th>
                          <th className="px-6 py-3 font-semibold">Type</th>
                          <th className="px-6 py-3 font-semibold">Dimensions</th>
                          <th className="px-6 py-3 font-semibold">Latency</th>
                        </tr>
                      </thead>
                      <tbody>
                        {cases.map((caseResult) => {
                        const row = caseDisplayRow(caseResult);
                        return (
                          <tr
                            key={caseResult.id}
                            className="border-b border-slate-200/60 last:border-b-0"
                          >
                            <td className="px-6 py-4 font-mono text-xs text-slate-600">
                              {row.caseId}
                            </td>
                            <td className="px-6 py-4 text-slate-600">
                              {row.caseType}
                            </td>
                            <td className="px-6 py-4">
                              <div className="flex flex-wrap gap-1.5">
                                {row.dimensions.map((dimension) => (
                                  <Badge
                                    key={dimension.label}
                                    variant={dimensionVariant(dimension.value)}
                                  >
                                    {dimension.label}: {dimension.value}
                                  </Badge>
                                ))}
                              </div>
                            </td>
                            <td className="px-6 py-4 text-slate-600">
                              {row.latencyLabel}
                            </td>
                          </tr>
                        );
                      })}
                      </tbody>
                    </table>
                  )}
                </section>
              </div>
            )}
          </div>

          <section className="app-panel mt-6 overflow-hidden rounded-[22px]">
            <div className="flex items-center justify-between border-b border-slate-200/70 p-6">
              <div>
                <h2 className="font-medium text-slate-900">
                  Release decision history
                </h2>
                <p className="mt-1 text-xs text-slate-400">
                  {historyLoading
                    ? "Loading…"
                    : `${decisionHistory.length} decision${
                        decisionHistory.length === 1 ? "" : "s"
                      }`}
                </p>
              </div>
              <Badge variant="violet">evaluation.read</Badge>
            </div>

            {historyLoading && decisionHistory.length === 0 ? (
              <div className="flex items-center gap-2 p-8 text-sm text-slate-400">
                <LoaderCircle className="h-4 w-4 animate-spin text-violet-500" />
                Loading release decisions...
              </div>
            ) : decisionHistory.length === 0 ? (
              <div className="p-10 text-center text-sm text-slate-400">
                No release decisions yet.
              </div>
            ) : (
              <div data-lenis-prevent className="max-h-[520px] overflow-y-auto">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-slate-200/70 bg-slate-50/70 text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    <tr>
                      <th className="px-6 py-3 font-semibold">Decision</th>
                      <th className="px-6 py-3 font-semibold">
                        Candidate run
                      </th>
                      <th className="px-6 py-3 font-semibold">Baseline</th>
                      <th className="px-6 py-3 font-semibold">Reviewer</th>
                      <th className="hidden px-6 py-3 font-semibold sm:table-cell">
                        Note
                      </th>
                      <th className="hidden px-6 py-3 font-semibold md:table-cell">
                        Created
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {decisionHistory.map((record) => {
                      const row = releaseDecisionRow(record);
                      return (
                        <tr
                          key={record.id}
                          className="border-b border-slate-200/60 last:border-b-0"
                        >
                          <td className="px-6 py-4">
                            <Badge variant={decisionVariant(row.decision)}>
                              {row.decisionLabel}
                            </Badge>
                          </td>
                          <td className="px-6 py-4 font-mono text-xs text-slate-600">
                            #{row.candidateRunId}
                          </td>
                          <td className="px-6 py-4 font-mono text-xs text-slate-600">
                            #{row.baselineId}
                          </td>
                          <td className="px-6 py-4 text-slate-600">
                            {row.reviewer}
                          </td>
                          <td className="hidden max-w-[280px] px-6 py-4 text-xs text-slate-500 sm:table-cell">
                            {row.note ?? "—"}
                          </td>
                          <td className="hidden px-6 py-4 text-xs text-slate-500 md:table-cell">
                            {row.createdLabel}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}