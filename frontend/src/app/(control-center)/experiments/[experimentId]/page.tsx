"use client";

import {
  ArrowLeft,
  Banknote,
  CalendarClock,
  CircleAlert,
  ClipboardCheck,
  Eye,
  FlaskConical,
  Link2,
  LoaderCircle,
  Printer,
  Scale,
  ShieldAlert,
  Target,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  Badge,
  ExperimentStatusBadge,
} from "@/components/experiments/status-badge";
import { useAuthorization } from "@/lib/authorization/context";
import {
  authorizationFeedback,
  planClientAuthorizationResponse,
} from "@/lib/authorization/helpers";
import {
  buildExperimentExecutiveSummary,
  canManageExperiments,
  deriveExperimentExperience,
  EXPERIMENT_CAUSALITY_DISCLAIMER,
  EXPERIMENT_MEASUREMENT_STATUS_MEASURED,
  EXPERIMENT_STATUS_ARCHIVED,
  EXPERIMENT_STATUS_CANCELLED,
  EXPERIMENT_STATUS_COMPLETED,
  EXPERIMENT_STATUS_DRAFT,
  EXPERIMENT_STATUS_RUNNING,
  experimentActions,
  experimentMeasurementStatusLabel,
  experimentMetricDefinition,
  experimentScopeLabel,
  experimentStatusHint,
  fetchExperiment,
  formatCount,
  formatExperimentWindowDays,
  formatMetricCell,
  formatNumber,
  formatRate,
  formatTimestamp,
  isExperimentPrimaryAction,
  runExperimentAction,
  type ExperimentComparisonDirection,
  type ExperimentComparisonRow,
  type ExperimentLifecycleAction,
  type ExperimentMeasurementSnapshot,
  type ServiceTransformationExperiment,
} from "@/lib/experiments/experiments";

function Panel({
  icon,
  title,
  subtitle,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="app-panel rounded-[22px] p-6 md:p-7">
      <div className="mb-5 flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-600">
          {icon}
        </div>
        <div>
          <h2 className="text-sm font-medium text-slate-900">{title}</h2>
          {subtitle && (
            <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>
          )}
        </div>
      </div>
      {children}
    </section>
  );
}

function NeutralDirectionChip({
  label,
  direction,
}: {
  label: string;
  direction: ExperimentComparisonDirection;
}) {
  if (direction === "not_applicable") {
    return (
      <Badge>
        {label}: n/a
      </Badge>
    );
  }
  return (
    <Badge variant="info">
      {label}: {direction}
    </Badge>
  );
}

function StatCell({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-slate-400">
        {label}
      </p>
      <p className="mt-1 text-sm font-medium text-slate-800">{value}</p>
    </div>
  );
}

function SnapshotPanel({
  snapshot,
  title,
  subtitle,
}: {
  snapshot: ExperimentMeasurementSnapshot;
  title: string;
  subtitle?: string;
}) {
  const rates = snapshot.rates;
  return (
    <Panel icon={<Eye className="h-4 w-4" />} title={title} subtitle={subtitle}>
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <Badge>{formatExperimentWindowDays(snapshot.window_days)}</Badge>
        <Badge variant="info">
          {formatTimestamp(snapshot.observed_at)}
        </Badge>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCell label="Autonomous execution" value={formatRate(rates.autonomous_execution_rate)} />
        <StatCell label="Autonomous executions" value={formatCount(snapshot.autonomous_executions)} />
        <StatCell label="Human approval" value={formatRate(rates.human_approval_rate)} />
        <StatCell label="Human approvals" value={formatCount(snapshot.human_approval_required)} />
        <StatCell label="Knowledge usage" value={formatRate(rates.knowledge_usage_rate)} />
        <StatCell label="Knowledge specialist runs" value={formatCount(snapshot.knowledge_specialist_runs)} />
        <StatCell label="Resolved tickets" value={formatCount(snapshot.tickets_resolved)} />
        <StatCell label="Reopened tickets" value={formatCount(snapshot.reopened_tickets)} />
        <StatCell label="Reopen rate" value={formatRate(rates.reopen_rate)} />
        <StatCell label="SLA breaches" value={formatCount(snapshot.total_sla_breaches)} />
        <StatCell label="Avg first response" value={snapshot.average_first_response_minutes === null ? "—" : `${formatNumber(snapshot.average_first_response_minutes)} min`} />
        <StatCell label="Avg resolution" value={snapshot.average_resolution_time_minutes === null ? "—" : `${formatNumber(snapshot.average_resolution_time_minutes)} min`} />
        <StatCell label="Agent runs" value={formatCount(snapshot.agent_runs)} />
      </div>

      {snapshot.value && (
        <div className="mt-5 rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
          <p className="mb-3 flex items-center gap-2 text-xs font-medium text-slate-500">
            <Banknote className="h-3.5 w-3.5" />
            Value block
            <Badge>
              {snapshot.value.measurement_status
                ? experimentMeasurementStatusLabel(snapshot.value.measurement_status)
                : "Not measured"}
            </Badge>
          </p>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCell label="Estimated minutes saved" value={formatCount(snapshot.value.estimated_minutes_saved)} />
            <StatCell label="Estimated hours saved" value={formatCount(snapshot.value.estimated_hours_saved)} />
            <StatCell label="Estimated net savings" value={snapshot.value.estimated_net_savings_usd === null ? "—" : `$${snapshot.value.estimated_net_savings_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
            <StatCell label="ROI" value={snapshot.value.roi_percent === null ? "—" : formatRate(snapshot.value.roi_percent)} />
          </div>
        </div>
      )}
    </Panel>
  );
}

function ComparisonTable({ rows }: { rows: ExperimentComparisonRow[] }) {
  const hasProjection = rows.some((row) => row.projected !== null);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-left text-xs">
        <thead>
          <tr className="border-b border-slate-200 text-[10px] uppercase tracking-wide text-slate-400">
            <th className="py-2.5 pr-3 font-medium">Metric</th>
            <th className="py-2.5 px-3 font-medium">Baseline</th>
            <th className="py-2.5 px-3 font-medium">Target</th>
            <th className="py-2.5 px-3 font-medium">Observed outcome</th>
            <th className="py-2.5 px-3 font-medium">Change vs baseline</th>
            <th className="py-2.5 px-3 font-medium">Variance vs target</th>
            {hasProjection && (
              <>
                <th className="py-2.5 px-3 font-medium">Projected</th>
                <th className="py-2.5 px-3 font-medium">Variance vs projection</th>
              </>
            )}
            <th className="py-2.5 pl-3 font-medium">Measured</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.metric} className="border-b border-slate-100 last:border-0">
              <td className="py-3 pr-3 align-top">
                <p className="font-medium text-slate-800">{row.label}</p>
                {row.helper && (
                  <p className="mt-0.5 max-w-[220px] text-[10px] leading-4 text-slate-400">
                    {row.helper}
                  </p>
                )}
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  <NeutralDirectionChip label="vs baseline" direction={row.directionVsBaseline} />
                  <NeutralDirectionChip label="vs target" direction={row.directionVsTarget} />
                </div>
              </td>
              <td className="py-3 px-3 align-top whitespace-nowrap text-slate-700">{row.baseline}</td>
              <td className="py-3 px-3 align-top whitespace-nowrap text-slate-700">{row.target}</td>
              <td className="py-3 px-3 align-top whitespace-nowrap font-medium text-slate-900">{row.observed}</td>
              <td className="py-3 px-3 align-top whitespace-nowrap text-slate-600">{row.changeFromBaseline}</td>
              <td className="py-3 px-3 align-top whitespace-nowrap text-slate-600">{row.varianceFromTarget}</td>
              {hasProjection && (
                <>
                  <td className="py-3 px-3 align-top whitespace-nowrap text-slate-600">{row.projected ?? "—"}</td>
                  <td className="py-3 px-3 align-top whitespace-nowrap text-slate-600">{row.varianceFromProjection ?? "—"}</td>
                </>
              )}
              <td className="py-3 pl-3 align-top whitespace-nowrap">
                <Badge variant="violet">{row.measurementStatusLabel}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LifecycleTimeline({ experiment }: { experiment: ServiceTransformationExperiment }) {
  const steps: { label: string; value: string; active: boolean }[] = [
    { label: "Created", value: formatTimestamp(experiment.created_at), active: true },
    {
      label: "Baseline captured",
      value: formatTimestamp(experiment.baseline_captured_at),
      active: experiment.status !== EXPERIMENT_STATUS_DRAFT,
    },
    {
      label: "Started",
      value: formatTimestamp(experiment.actual_started_at),
      active:
        experiment.status === EXPERIMENT_STATUS_RUNNING ||
        experiment.status === EXPERIMENT_STATUS_COMPLETED ||
        experiment.status === EXPERIMENT_STATUS_CANCELLED,
    },
    {
      label: "Completed & measured",
      value: formatTimestamp(experiment.measured_at),
      active: experiment.status === EXPERIMENT_STATUS_COMPLETED,
    },
    {
      label: "Archived",
      value: formatTimestamp(experiment.archived_at),
      active: experiment.status === EXPERIMENT_STATUS_ARCHIVED,
    },
  ];

  return (
    <ol className="grid gap-2 sm:grid-cols-2">
      {steps.map((step) => (
        <li
          key={step.label}
          className={`rounded-2xl border p-3 ${
            step.active
              ? "border-violet-100 bg-violet-50/40"
              : "border-slate-200/70 bg-[#fbfcff] opacity-60"
          }`}
        >
          <p className="text-xs font-medium text-slate-700">{step.label}</p>
          <p className="mt-1 text-xs text-slate-500">{step.value}</p>
        </li>
      ))}
    </ol>
  );
}

export default function ExperimentDetailPage() {
  const params = useParams<{ experimentId: string }>();
  const { can, refresh } = useAuthorization();
  const experience = deriveExperimentExperience(can);
  const canManage = canManageExperiments(can);

  const experimentId = Number(params.experimentId);
  const validId = Number.isInteger(experimentId) && experimentId > 0;

  const [experiment, setExperiment] = useState<ServiceTransformationExperiment | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [busyAction, setBusyAction] = useState<ExperimentLifecycleAction | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<ExperimentLifecycleAction | null>(null);
  const [actionError, setActionError] = useState("");

  const loadExperiment = useCallback(async () => {
    if (!validId) {
      setLoadError("This experiment does not exist.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError("");
    try {
      setExperiment(await fetchExperiment(experimentId));
    } catch (err) {
      if (err instanceof Object && "status" in err && "detail" in err) {
        const plan = planClientAuthorizationResponse(
          (err as { status: number }).status,
          (err as { detail: string | null }).detail,
        );
        const feedback = authorizationFeedback(plan);
        if (feedback) {
          setLoadError(feedback);
          refresh();
          return;
        }
      }
      setLoadError(
        err instanceof Error
          ? err.message
          : "Could not load the experiment.",
      );
    } finally {
      setLoading(false);
    }
  }, [refresh, experimentId, validId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadExperiment();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadExperiment]);

  const summary = useMemo(
    () => (experiment ? buildExperimentExecutiveSummary(experiment) : null),
    [experiment],
  );

  const handleAction = useCallback(
    async (action: ExperimentLifecycleAction) => {
      if (action === "cancel" || action === "archive") {
        if (pendingConfirm !== action) {
          setPendingConfirm(action);
          return;
        }
        setPendingConfirm(null);
      }
      setBusyAction(action);
      setActionError("");
      try {
        const updated = await runExperimentAction(experimentId, action);
        setExperiment(updated);
        setPendingConfirm(null);
      } catch (err) {
        if (err instanceof Object && "status" in err && "detail" in err) {
          const plan = planClientAuthorizationResponse(
            (err as { status: number }).status,
            (err as { detail: string | null }).detail,
          );
          const feedback = authorizationFeedback(plan);
          if (feedback) {
            setActionError(feedback);
            refresh();
            return;
          }
        }
        setActionError(
          err instanceof Error
            ? err.message
            : "The experiment could not be updated.",
        );
      } finally {
        setBusyAction(null);
      }
    },
    [experimentId, pendingConfirm, refresh],
  );

  if (loadError) {
    return (
      <div className="min-h-screen">
        <div className="xl:pl-[230px]">
          <main className="mx-auto max-w-[1100px] px-6 pb-16 pt-[112px] lg:px-10">
            <div className="flex flex-col items-center gap-3 rounded-[20px] border border-rose-200 bg-rose-50/60 p-10 text-center">
              <TriangleAlert className="h-6 w-6 text-rose-500" />
              <p className="text-sm text-rose-700">{loadError}</p>
              <Link
                href="/experiments"
                className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-700 transition hover:text-violet-700"
              >
                Back to experiments
              </Link>
            </div>
          </main>
        </div>
      </div>
    );
  }

  if (loading || !experiment) {
    return (
      <div className="min-h-screen">
        <div className="xl:pl-[230px]">
          <main className="mx-auto max-w-[1100px] px-6 pb-16 pt-[112px] lg:px-10">
            <div className="flex items-center justify-center gap-3 rounded-[20px] border border-slate-200 bg-white/70 p-10">
              <LoaderCircle className="h-5 w-5 animate-spin text-violet-500" />
              <p className="text-sm text-slate-500">Loading experiment…</p>
            </div>
          </main>
        </div>
      </div>
    );
  }

  const actions = canManage ? experimentActions(experiment.status) : [];
  const hint = experimentStatusHint(experiment.status);
  const hasComparison = experiment.outcome_comparison !== null;
  const comparisonRows = summary?.comparisonRows ?? [];
  const hasValueRows =
    summary !== null &&
    summary.valueRows !== null &&
    summary.valueRows !== undefined &&
    summary.valueRows.length > 0;

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="no-print fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1100px] items-center justify-between gap-4 px-6 lg:px-10">
            <div className="flex min-w-0 items-center gap-3">
              <Link
                href="/experiments"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:text-slate-800"
                aria-label="Back to experiments"
              >
                <ArrowLeft className="h-4 w-4" />
              </Link>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold tracking-[-0.03em] text-slate-950">
                  {experiment.name}
                </p>
                <p className="hidden text-[11px] text-slate-400 sm:block">
                  {experimentScopeLabel(experiment.scope_type, experiment.scope_key)} ·{" "}
                  {experiment.measurement_status
                    ? experimentMeasurementStatusLabel(experiment.measurement_status)
                    : "Not yet measured"}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {actions.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  {actions.map((action) => {
                    const primary = isExperimentPrimaryAction(action);
                    const busy = busyAction === action;
                    const confirming = pendingConfirm === action;
                    return (
                      <button
                        key={action}
                        type="button"
                        onClick={() => void handleAction(action)}
                        disabled={busy}
                        className={
                          primary
                            ? "flex h-10 items-center gap-2 rounded-xl bg-violet-600 px-4 text-xs font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
                            : confirming
                              ? "flex h-10 items-center gap-2 rounded-xl border border-rose-300 bg-rose-50 px-4 text-xs font-medium text-rose-700 transition hover:bg-rose-100"
                              : "flex h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-xs font-medium text-slate-600 transition hover:border-violet-300 hover:text-violet-700 disabled:opacity-50"
                        }
                      >
                        {busy && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
                        {confirming && action === "cancel"
                          ? "Confirm cancel"
                          : confirming && action === "archive"
                            ? "Confirm archive"
                            : actionLabel(action)}
                      </button>
                    );
                  })}
                </div>
              )}

              <button
                type="button"
                onClick={() => window.print()}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600"
                aria-label="Print experiment"
              >
                <Printer className="h-4 w-4" />
              </button>
            </div>
          </div>
        </header>

        <main className="print-area mx-auto max-w-[1100px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="print-summary mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Experiment outcome review
            </p>
            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              {experiment.name}
            </h1>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <ExperimentStatusBadge status={experiment.status} />
              <Badge>{experimentScopeLabel(experiment.scope_type, experiment.scope_key)}</Badge>
              <Badge variant="info">
                Baseline {formatExperimentWindowDays(experiment.baseline_window_days)} · Measurement{" "}
                {formatExperimentWindowDays(experiment.measurement_window_days)}
              </Badge>
              {experiment.comparison_version && (
                <Badge variant="violet">formula v{experiment.comparison_version}</Badge>
              )}
            </div>
            {hint && <p className="mt-4 text-sm leading-6 text-slate-500">{hint}</p>}
          </section>

          {actionError && (
            <div className="mb-8 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
              {actionError}
            </div>
          )}

          <section className="mb-8 rounded-[20px] border border-amber-200 bg-amber-50/55 p-5">
            <div className="flex gap-3">
              <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
              <div>
                <p className="text-sm font-medium text-amber-800">
                  Observed change is not cause
                </p>
                <p className="mt-1 max-w-3xl text-xs leading-5 text-amber-700/80">
                  {EXPERIMENT_CAUSALITY_DISCLAIMER}
                </p>
              </div>
            </div>
          </section>

          {experience.readOnly && (
            <div className="mb-8 rounded-[20px] border border-blue-200 bg-blue-50/55 p-5">
              <div className="flex gap-3">
                <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-blue-500" />
                <div>
                  <p className="text-sm font-medium text-blue-800">
                    Read-only access
                  </p>
                  <p className="mt-1 text-xs leading-5 text-blue-700/80">
                    You can view this experiment. Lifecycle actions require the
                    manage capability.
                  </p>
                </div>
              </div>
            </div>
          )}

          <section className="mb-8 space-y-6">
            <Panel
              icon={<CalendarClock className="h-4 w-4" />}
              title="Lifecycle"
              subtitle="The backend state machine is the sole authority for when each transition is allowed."
            >
              <LifecycleTimeline experiment={experiment} />
            </Panel>

            {hasComparison && summary && summary.narrative.length > 0 && (
              <Panel
                icon={<Scale className="h-4 w-4" />}
                title="Executive summary"
                subtitle="Deterministic narrative rendered from the persisted comparison — no inference."
              >
                <div className="space-y-3">
                  {summary.narrative.map((sentence, index) => (
                    <p key={index} className="text-sm leading-6 text-slate-700">
                      {sentence}
                    </p>
                  ))}
                </div>
              </Panel>
            )}

            <Panel
              icon={<ClipboardCheck className="h-4 w-4" />}
              title="Hypothesis"
              subtitle="The intended change and the explicit targets recorded at creation."
            >
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
                  <p className="text-[10px] uppercase tracking-wide text-slate-400">Hypothesis summary</p>
                  <p className="mt-1.5 text-sm leading-6 text-slate-800">
                    {experiment.hypothesis.summary}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
                  <p className="text-[10px] uppercase tracking-wide text-slate-400">Change description</p>
                  <p className="mt-1.5 text-sm leading-6 text-slate-800">
                    {experiment.hypothesis.change_description ?? "—"}
                  </p>
                </div>
              </div>
            </Panel>

            {experiment.source_scenario_snapshot && (
              <Panel
                icon={<Link2 className="h-4 w-4" />}
                title="Source projection"
                subtitle="This links the experiment to an evaluated scenario. It does not apply the scenario to production."
              >
                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <StatCell label="Scenario name" value={experiment.source_scenario_snapshot.name} />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <StatCell label="Window" value={`${formatExperimentWindowDays(experiment.source_scenario_snapshot.window_days)} (${experiment.source_scenario_snapshot.status})`} />
                    <StatCell label="Formula version" value={experiment.source_scenario_snapshot.formula_version ?? "—"} />
                  </div>
                </div>
              </Panel>
            )}

            {experiment.baseline_snapshot && (
              <SnapshotPanel
                snapshot={experiment.baseline_snapshot}
                title="Immutable baseline"
                subtitle="Captured from live telemetry at baseline capture; it never changes."
              />
            )}

            <Panel
              icon={<Target className="h-4 w-4" />}
              title="Target metrics"
              subtitle="Explicit targets recorded at creation."
            >
              <div className="grid gap-3 sm:grid-cols-2">
                {Object.entries(experiment.target_metrics).map(([key, value]) => {
                  const definition = experimentMetricDefinition(key);
                  return (
                    <div key={key} className="flex items-center justify-between rounded-2xl border border-slate-200/70 bg-[#fbfcff] px-4 py-3">
                      <div>
                        <p className="text-sm font-medium text-slate-800">
                          {definition?.label ?? key}
                        </p>
                        {experiment.hypothesis.expected_direction?.[key] && (
                          <p className="mt-0.5 text-[10px] uppercase tracking-wide text-slate-400">
                            Expected to {experiment.hypothesis.expected_direction[key]}
                          </p>
                        )}
                      </div>
                      <p className="text-sm font-semibold text-slate-900">
                        {formatMetricCell(value, definition?.kind ?? "count")}
                      </p>
                    </div>
                  );
                })}
              </div>
            </Panel>

            {experiment.observed_outcome && (
              <SnapshotPanel
                snapshot={experiment.observed_outcome}
                title="Observed outcome"
                subtitle="Measured change during the experiment window."
              />
            )}

            {hasComparison && comparisonRows.length > 0 && (
              <Panel
                icon={<Scale className="h-4 w-4" />}
                title="Comparison"
                subtitle="Backend-computed differences. Rate differences are percentage points, never percentages."
              >
                <ComparisonTable rows={comparisonRows} />
                {summary && summary.warnings.length > 0 && (
                  <div className="mt-4 space-y-2">
                    {summary.warnings.map((warning) => (
                      <p key={warning} className="flex items-start gap-2 text-xs leading-5 text-amber-700">
                        <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                        {warning}
                      </p>
                    ))}
                  </div>
                )}
              </Panel>
            )}

            {hasComparison && summary && (
              <Panel
                icon={<Banknote className="h-4 w-4" />}
                title="Value and ROI"
                subtitle="Projected vs observed vs variance — backend values only."
              >
                {hasValueRows ? (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[520px] border-collapse text-left text-xs">
                      <thead>
                        <tr className="border-b border-slate-200 text-[10px] uppercase tracking-wide text-slate-400">
                          <th className="py-2.5 pr-3 font-medium">Metric</th>
                          <th className="py-2.5 px-3 font-medium">Projected</th>
                          <th className="py-2.5 px-3 font-medium">Observed</th>
                          <th className="py-2.5 pl-3 font-medium">Variance vs projection</th>
                        </tr>
                      </thead>
                      <tbody>
                        {summary.valueRows!.map((row) => (
                          <tr key={row.label} className="border-b border-slate-100 last:border-0">
                            <td className="py-3 pr-3 font-medium text-slate-800">{row.label}</td>
                            <td className="py-3 px-3 text-slate-600">{row.projected}</td>
                            <td className="py-3 px-3 font-medium text-slate-900">{row.observed}</td>
                            <td className="py-3 pl-3 text-slate-600">{row.variance}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-sm leading-6 text-slate-500">
                    Value and ROI are unavailable for this experiment scope or
                    measurement. An unavailable value or ROI renders as “—” and
                    is never recomputed or defaulted to zero.
                  </p>
                )}
                {summary.measurementStatusLabel && (
                  <div className="mt-4 rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
                    <p className="text-[10px] uppercase tracking-wide text-slate-400">
                      Measurement status
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                      <Badge variant="violet">{summary.measurementStatusLabel}</Badge>
                      {experiment.measurement_status !== EXPERIMENT_MEASUREMENT_STATUS_MEASURED && (
                        <span className="text-xs text-slate-500">
                          {experiment.measurement_status === "insufficient_sample"
                            ? "The minimum autonomous-execution sample was not met."
                            : experiment.measurement_status === "pricing_unavailable"
                              ? "LLM pricing is not configured, so value and ROI cannot be measured."
                              : experiment.measurement_status === "no_observed_activity"
                                ? "No ticket or run activity was observed in the measurement window."
                                : experiment.measurement_status === "incomplete_window"
                                  ? "The measurement window had not fully elapsed."
                                  : null}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </Panel>
            )}

            <Panel
              icon={<FlaskConical className="h-4 w-4" />}
              title="Limitations"
              subtitle="Persisted verbatim from the outcome engine — always shown, never hidden."
            >
              <div className="space-y-2">
                {(summary?.limitations ?? [EXPERIMENT_CAUSALITY_DISCLAIMER]).map(
                  (limitation) => (
                    <p key={limitation} className="flex items-start gap-2 text-xs leading-5 text-slate-500">
                      <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                      {limitation}
                    </p>
                  ),
                )}
              </div>
            </Panel>

            <Panel icon={<CalendarClock className="h-4 w-4" />} title="Metadata">
              <div className="grid gap-4 sm:grid-cols-3">
                <StatCell label="Created at" value={formatTimestamp(experiment.created_at)} />
                <StatCell label="Created by" value={experiment.created_by_subject ?? "—"} />
                <StatCell label="Updated" value={formatTimestamp(experiment.updated_at)} />
                <StatCell label="Measurement status" value={experiment.measurement_status ? experimentMeasurementStatusLabel(experiment.measurement_status) : "Not yet measured"} />
                <StatCell label="Comparison version" value={experiment.comparison_version ?? "—"} />
                <StatCell
                  label="Archiving reason"
                  value={experiment.archiving_reason ?? "—"}
                />
              </div>
            </Panel>
          </section>
        </main>
      </div>
    </div>
  );
}

function actionLabel(action: ExperimentLifecycleAction): string {
  switch (action) {
    case "capture-baseline":
      return "Capture baseline";
    case "start":
      return "Start experiment";
    case "complete":
      return "Complete & measure";
    case "cancel":
      return "Cancel experiment";
    case "archive":
      return "Archive experiment";
  }
}