"use client";

import {
  Archive,
  ArrowLeft,
  BadgeCheck,
  CircleAlert,
  FileText,
  LoaderCircle,
  PlayCircle,
  Printer,
  ShieldAlert,
  SlidersHorizontal,
  TriangleAlert,
  Unplug,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
  Badge,
  SimulationStatusBadge,
} from "@/components/simulation/status-badge";
import { useAuthorization } from "@/lib/authorization/context";
import {
  authorizationFeedback,
  planClientAuthorizationResponse,
} from "@/lib/authorization/helpers";
import {
  ARCHIVED_CANNOT_RE_EVALUATE,
  archiveSimulation,
  assumptionDefinition,
  canManageSimulations,
  deriveSimulationExperience,
  EVALUATE_ACTION_LABEL,
  evaluateSimulation,
  fetchSimulation,
  formatAssumptionSummary,
  formatCount,
  formatDelta,
  formatMoney,
  formatMeasurementStatusLabel,
  formatRate,
  formatTimestamp,
  formatWindowLabel,
  presentationDelta,
  ROI_UNAVAILABLE_MESSAGE,
  REEVALUATE_ACTION_LABEL,
  SIMULATION_DISCLAIMER,
  SIMULATION_ASSUMPTION_KEYS,
  SIMULATION_STATUS_ARCHIVED,
  SIMULATION_STATUS_EVALUATED,
  UNCHANGED_NOT_MODELLED,
  UNEVALUATED_PROMPT,
  executiveLimitationsFor,
  buildExecutiveSummary,
  deltaToneClass,
  type ServiceTransformationScenario,
} from "@/lib/simulation/simulation";

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
          {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

function renderDeltaCell(delta: number | null) {
  if (delta === null) {
    return "—";
  }
  return (
    <span className={deltaToneClass(delta)}>{formatDelta(delta)}</span>
  );
}

function MetricCard({
  label,
  value,
  delta,
  tone = "default",
}: {
  label: string;
  value: string;
  delta?: number | null;
  tone?: "default" | "violet";
}) {
  return (
    <div
      className={`rounded-2xl border p-4 ${
        tone === "violet" ? "border-violet-100 bg-violet-50/40" : "border-slate-200/70 bg-[#fbfcff]"
      }`}
    >
      <p className="text-[11px] uppercase tracking-wide text-slate-400">{label}</p>
      <div className="mt-1.5 flex items-baseline gap-2">
        <p className="text-xl font-medium tracking-[-0.02em] text-slate-900">
          {value}
        </p>
        {delta !== undefined && delta !== null && (
          <span className={`text-xs font-medium ${deltaToneClass(delta)}`}>
            {formatDelta(delta)}
          </span>
        )}
      </div>
    </div>
  );
}

export default function ScenarioDetailPage() {
  const params = useParams<{ scenarioId: string }>();
  const { can, refresh } = useAuthorization();
  const experience = deriveSimulationExperience(can);
  const canManage = canManageSimulations(can);

  const scenarioId = Number(params.scenarioId);
  const validId = Number.isInteger(scenarioId) && scenarioId > 0;

  const [scenario, setScenario] = useState<ServiceTransformationScenario | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState<null | "evaluate" | "archive">(null);
  const [actionError, setActionError] = useState("");

  const loadScenario = useCallback(async () => {
    if (!validId) {
      setLoadError("This scenario does not exist.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError("");
    try {
      setScenario(await fetchSimulation(scenarioId));
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
          : "Could not load the transformation scenario.",
      );
    } finally {
      setLoading(false);
    }
  }, [refresh, scenarioId, validId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadScenario();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadScenario]);

  const handleEvaluate = useCallback(async () => {
    if (!scenario) return;
    setBusy("evaluate");
    setActionError("");
    try {
      await evaluateSimulation(scenario.id);
      await loadScenario();
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
          : "Could not evaluate the scenario.",
      );
    } finally {
      setBusy(null);
    }
  }, [loadScenario, refresh, scenario]);

  const handleArchive = useCallback(async () => {
    if (!scenario) return;
    setBusy("archive");
    setActionError("");
    try {
      await archiveSimulation(scenario.id);
      await loadScenario();
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
        err instanceof Error ? err.message : "Could not archive the scenario.",
      );
    } finally {
      setBusy(null);
    }
  }, [loadScenario, refresh, scenario]);

  if (!validId) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <p className="rounded-2xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
          This scenario does not exist.
        </p>
      </div>
    );
  }

  const evaluated = Boolean(scenario?.observed_baseline && scenario?.projected_result);
  const archived = scenario?.status === SIMULATION_STATUS_ARCHIVED;
  const measurementStatus = scenario?.projected_result?.value.measurement_status;

  const projectedMetrics: {
    label: string;
    observed: string;
    projected: string;
    delta: number | null;
  }[] = [];

  if (scenario && evaluated && scenario.observed_baseline && scenario.projected_result) {
    const baseline = scenario.observed_baseline;
    const projected = scenario.projected_result;
    projectedMetrics.push(
      {
        label: "Autonomous executions",
        observed: formatCount(baseline.autonomous_executions),
        projected: formatCount(projected.autonomous_executions),
        delta: presentationDelta(
          projected.autonomous_executions,
          baseline.autonomous_executions,
        ),
      },
      {
        label: "Human approvals",
        observed: formatCount(baseline.human_approval_required),
        projected: formatCount(projected.human_approval_required),
        delta: presentationDelta(
          projected.human_approval_required,
          baseline.human_approval_required,
        ),
      },
      {
        label: "Knowledge specialist runs",
        observed: formatCount(baseline.knowledge_specialist_runs),
        projected: formatCount(projected.knowledge_specialist_runs),
        delta: presentationDelta(
          projected.knowledge_specialist_runs,
          baseline.knowledge_specialist_runs,
        ),
      },
      {
        label: "Reopened tickets",
        observed: formatCount(baseline.reopened_tickets),
        projected: formatCount(projected.reopened_tickets),
        delta: presentationDelta(
          projected.reopened_tickets,
          baseline.reopened_tickets,
        ),
      },
      {
        label: "SLA breaches",
        observed: formatCount(baseline.total_sla_breaches),
        projected: formatCount(projected.total_sla_breaches),
        delta: presentationDelta(
          projected.total_sla_breaches,
          baseline.total_sla_breaches,
        ),
      },
    );
  }

  const summary = scenario ? buildExecutiveSummary(scenario) : null;

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="no-print fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between gap-4 px-6 lg:px-10">
            <div className="flex items-center gap-3">
              <Link
                href="/simulations"
                className="flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:border-violet-300 hover:text-violet-700"
                aria-label="Back to simulations"
              >
                <ArrowLeft className="h-4 w-4" />
              </Link>
              <div>
                <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                  {scenario ? scenario.name : "Scenario"}
                </p>
                <p className="hidden text-[11px] text-slate-400 sm:block">
                  {scenario ? formatWindowLabel(scenario.window_days) : "Loading…"}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {scenario && (
                <button
                  type="button"
                  onClick={() => window.print()}
                  className="flex h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-600 transition hover:border-violet-300 hover:text-violet-700"
                >
                  <Printer className="h-4 w-4" />
                  Print
                </button>
              )}

              {scenario && canManage && !archived && (
                <button
                  type="button"
                  onClick={() => void handleEvaluate()}
                  disabled={busy !== null}
                  className="flex h-10 items-center gap-2 rounded-xl bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
                >
                  {busy === "evaluate" ? (
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  ) : (
                    <PlayCircle className="h-4 w-4" />
                  )}
                  {scenario.status === SIMULATION_STATUS_EVALUATED
                    ? REEVALUATE_ACTION_LABEL
                    : EVALUATE_ACTION_LABEL}
                </button>
              )}

              {scenario && canManage && !archived && (
                <button
                  type="button"
                  onClick={() => void handleArchive()}
                  disabled={busy !== null}
                  className="flex h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-600 transition hover:border-rose-300 hover:text-rose-700 disabled:opacity-50"
                >
                  {busy === "archive" ? (
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  ) : (
                    <Archive className="h-4 w-4" />
                  )}
                  Archive
                </button>
              )}
            </div>
          </div>
        </header>

        <main className="print-area mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          {loading && !scenario ? (
            <div className="my-10 flex items-center justify-center gap-3 rounded-[20px] border border-slate-200 bg-white/70 p-10">
              <LoaderCircle className="h-5 w-5 animate-spin text-violet-500" />
              <p className="text-sm text-slate-500">
                Loading transformation scenario…
              </p>
            </div>
          ) : loadError && !scenario ? (
            <div className="my-10 flex flex-col items-center gap-3 rounded-[20px] border border-rose-200 bg-rose-50/60 p-10 text-center">
              <TriangleAlert className="h-6 w-6 text-rose-500" />
              <p className="text-sm text-rose-700">{loadError}</p>
              <button
                type="button"
                onClick={() => void loadScenario()}
                className="rounded-xl border border-rose-200 bg-white px-4 py-2 text-xs font-medium text-rose-700 transition hover:bg-rose-50"
              >
                Try again
              </button>
            </div>
          ) : !scenario ? (
            <p className="my-10 text-sm text-slate-500">
              This scenario does not exist.
            </p>
          ) : (
            <>
              <section className="no-print mb-8">
                <div className="flex flex-wrap items-center gap-2">
                  <SimulationStatusBadge status={scenario.status} />
                  <Badge variant="violet">
                    {formatWindowLabel(scenario.window_days)}
                  </Badge>
                  {scenario.formula_version && (
                    <Badge variant="info">v{scenario.formula_version}</Badge>
                  )}
                  {measurementStatus && (
                    <Badge variant="warning">
                      {formatMeasurementStatusLabel(measurementStatus)}
                    </Badge>
                  )}
                </div>

                <h1 className="mt-4 text-3xl font-light tracking-[-0.045em] text-slate-950 md:text-4xl">
                  {scenario.name}
                </h1>
                {scenario.description && (
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
                    {scenario.description}
                  </p>
                )}

                <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-400">
                  <span>
                    Last evaluated: {formatTimestamp(scenario.evaluated_at)}
                  </span>
                  {scenario.created_by_subject && (
                    <span>Created by: {scenario.created_by_subject}</span>
                  )}
                </div>
              </section>

              {experience.readOnly && (
                <div className="no-print mb-8 rounded-[20px] border border-blue-200 bg-blue-50/55 p-5">
                  <div className="flex gap-3">
                    <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-blue-500" />
                    <p className="text-xs leading-5 text-blue-700/80">
                      Read-only access — you can review and print this scenario,
                      but only operators with the manage capability can evaluate
                      or archive it.
                    </p>
                  </div>
                </div>
              )}

              {archived && (
                <div className="mb-8 rounded-[20px] border border-slate-200 bg-slate-50 p-5">
                  <div className="flex gap-3">
                    <Archive className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
                    <div>
                      <p className="text-sm font-medium text-slate-700">
                        Archived scenario
                      </p>
                      <p className="mt-1 text-xs leading-5 text-slate-500">
                        {ARCHIVED_CANNOT_RE_EVALUATE} The persisted snapshot and
                        projected results remain available for review.
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {!evaluated && !archived && (
                <div className="mb-8 rounded-[20px] border border-amber-200 bg-amber-50/55 p-5">
                  <div className="flex gap-3">
                    <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
                    <p className="text-xs leading-5 text-amber-700/80">
                      {UNEVALUATED_PROMPT} The assumptions are defined, but no
                      observed baseline has been captured yet.
                    </p>
                  </div>
                </div>
              )}

              <div className="mb-8 rounded-[20px] border border-amber-200 bg-amber-50/55 p-5">
                <div className="flex gap-3">
                  <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
                  <p className="text-xs leading-5 text-amber-700/80">
                    {SIMULATION_DISCLAIMER} Scenario models never mutate live
                    tickets, runs, SLA policies, or authorization rules.
                  </p>
                </div>
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <Panel
                  icon={<Unplug className="h-4 w-4" />}
                  title="Observed baseline"
                  subtitle="Captured from live service data at evaluation time"
                >
                  {!evaluated || !scenario.observed_baseline ? (
                    <p className="text-xs leading-5 text-slate-400">
                      {UNEVALUATED_PROMPT}
                    </p>
                  ) : (
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                      <MetricCard
                        label="Autonomous execution"
                        value={formatRate(scenario.observed_baseline.rates.autonomous_execution_rate)}
                      />
                      <MetricCard
                        label="Human approval"
                        value={formatRate(scenario.observed_baseline.rates.human_approval_rate)}
                      />
                      <MetricCard
                        label="Knowledge usage"
                        value={formatRate(scenario.observed_baseline.rates.knowledge_usage_rate)}
                      />
                      <MetricCard
                        label="Resolved tickets"
                        value={formatCount(scenario.observed_baseline.tickets_resolved)}
                      />
                      <MetricCard
                        label="Reopened tickets"
                        value={formatCount(scenario.observed_baseline.reopened_tickets)}
                      />
                      <MetricCard
                        label="Reopen rate"
                        value={formatRate(scenario.observed_baseline.rates.reopen_rate)}
                      />
                      <MetricCard
                        label="SLA breaches"
                        value={formatCount(scenario.observed_baseline.total_sla_breaches)}
                      />
                    </div>
                  )}
                </Panel>

                <Panel
                  icon={<SlidersHorizontal className="h-4 w-4" />}
                  title="Assumptions"
                  subtitle="Enabled dimensions applied to the observed baseline"
                >
                  <ul className="space-y-2.5">
                    {SIMULATION_ASSUMPTION_KEYS.map((key) => {
                      const definition = assumptionDefinition(key);
                      const value = scenario.assumptions[key];
                      if (definition) {
                        return (
                          <li
                            key={key}
                            className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200/70 bg-[#fbfcff] px-3 py-2.5"
                          >
                            <div>
                              <p className="text-xs font-medium text-slate-800">
                                {definition.label}
                              </p>
                              {value === null || value === undefined ? (
                                <p className="mt-0.5 text-[11px] italic text-slate-400">
                                  {UNCHANGED_NOT_MODELLED}
                                </p>
                              ) : (
                                <p className="mt-0.5 text-[11px] text-slate-400">
                                  {definition.helper}
                                </p>
                              )}
                            </div>
                            <span className="text-sm font-medium text-slate-900">
                              {value === null || value === undefined
                                ? "—"
                                : formatRate(value)}
                            </span>
                          </li>
                        );
                      }
                      return (
                        <li
                          key={key}
                          className="flex items-center justify-between rounded-xl border border-slate-200/70 bg-[#fbfcff] px-3 py-2.5"
                        >
                          <span className="text-xs text-slate-500">{key}</span>
                          <span className="text-sm">
                            {value === null || value === undefined
                              ? "—"
                              : formatRate(value)}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                  <p className="mt-3 text-[11px] leading-5 text-slate-400">
                    {formatAssumptionSummary(scenario.assumptions) || UNCHANGED_NOT_MODELLED}
                  </p>
                </Panel>
              </div>

              <div className="mt-6">
                <Panel
                  icon={<BadgeCheck className="h-4 w-4" />}
                  title="Projected result"
                  subtitle="Deterministic output of the simulation engine"
                >
                  {!evaluated || !scenario.projected_result ? (
                    <p className="text-xs leading-5 text-slate-400">
                      {UNEVALUATED_PROMPT}
                    </p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-400">
                            <th className="pb-2 font-medium">Metric</th>
                            <th className="pb-2 pr-4 text-right font-medium">
                              Observed
                            </th>
                            <th className="pb-2 pr-4 text-right font-medium">
                              Projected
                            </th>
                            <th className="pb-2 text-right font-medium">
                              Delta
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {projectedMetrics.map((metric) => (
                            <tr
                              key={metric.label}
                              className="border-b border-slate-100"
                            >
                              <td className="py-3 text-xs font-medium text-slate-700">
                                {metric.label}
                              </td>
                              <td className="py-3 pr-4 text-right tabular-nums text-slate-500">
                                {metric.observed}
                              </td>
                              <td className="py-3 pr-4 text-right tabular-nums font-medium text-slate-900">
                                {metric.projected}
                              </td>
                              <td className="py-3 text-right tabular-nums text-xs">
                                {renderDeltaCell(metric.delta)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </Panel>
              </div>

              <div className="mt-6 grid gap-6 lg:grid-cols-2">
                <Panel
                  icon={<FileText className="h-4 w-4" />}
                  title="Value & ROI"
                  subtitle="Quantified impact as reported by the engine"
                >
                  {!evaluated || !scenario.projected_result ? (
                    <p className="text-xs leading-5 text-slate-400">
                      {UNEVALUATED_PROMPT}
                    </p>
                  ) : (
                    <>
                      <div className="space-y-2.5">
                        {(
                          [
                            {
                              label: "Estimated minutes saved (projected)",
                              value: scenario.projected_result.value.estimated_minutes_saved,
                            },
                            {
                              label: "Estimated hours saved (projected)",
                              value: scenario.projected_result.value.estimated_hours_saved,
                            },
                            {
                              label: "Estimated labor savings (projected)",
                              value: scenario.projected_result.value.estimated_labor_savings_usd,
                            },
                            {
                              label: "Projected AI cost",
                              value: scenario.projected_result.value.agent_ai_cost_usd,
                            },
                            {
                              label: "Estimated net savings (projected)",
                              value: scenario.projected_result.value.estimated_net_savings_usd,
                            },
                          ] as const
                        ).map((row) => (
                          <div
                            key={row.label}
                            className="flex items-center justify-between rounded-xl border border-slate-200/70 bg-[#fbfcff] px-3 py-2.5"
                          >
                            <span className="text-xs text-slate-500">
                              {row.label}
                            </span>
                            <span className="text-sm font-medium tabular-nums text-slate-900">
                              {formatMoney(row.value)}
                            </span>
                          </div>
                        ))}
                      </div>

                      <div className="mt-4 flex items-center justify-between rounded-xl border border-violet-200 bg-violet-50/60 px-3 py-3">
                        <span className="text-xs font-medium text-violet-700">
                          Projected ROI
                        </span>
                        <span className="text-base font-semibold tabular-nums text-violet-800">
                          {scenario.projected_result.value.roi_percent === null
                            ? "—"
                            : formatRate(scenario.projected_result.value.roi_percent)}
                        </span>
                      </div>

                      {scenario.projected_result.value.roi_percent === null && (
                        <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/60 px-3 py-2.5">
                          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                          <p className="text-[11px] leading-5 text-amber-700/80">
                            {ROI_UNAVAILABLE_MESSAGE}
                          </p>
                        </div>
                      )}
                    </>
                  )}
                </Panel>

                <Panel
                  icon={<TriangleAlert className="h-4 w-4" />}
                  title="Model limitations"
                  subtitle="Stated because they are material to every decision"
                >
                  {executiveLimitationsFor(scenario).length === 0 ? (
                    <p className="text-xs leading-5 text-slate-400">
                      No modelled limitations for this scenario.
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {executiveLimitationsFor(scenario).map((limitation) => (
                        <li
                          key={limitation}
                          className="flex items-start gap-2 text-xs leading-5 text-slate-600"
                        >
                          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                          {limitation}
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>
              </div>

              {summary && (
                <div className="mt-6">
                  <Panel
                    icon={<SlidersHorizontal className="h-4 w-4" />}
                    title="Executive summary"
                    subtitle="Deterministic snapshot for decision support"
                  >
                    <div className="print-summary space-y-5">
                      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-slate-500">
                        <span>
                          Window: <span className="font-medium text-slate-700">{summary.windowLabel}</span>
                        </span>
                        <span>
                          Status: <span className="font-medium text-slate-700">{summary.statusLabel}</span>
                        </span>
                        <span>
                          Evaluated: <span className="font-medium text-slate-700">{summary.evaluatedAtLabel}</span>
                        </span>
                      </div>

                      {summary.observed.length > 0 && (
                        <div>
                          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                            Observed
                          </h3>
                          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                            {summary.observed.map((metric) => (
                              <div
                                key={metric.label}
                                className="rounded-xl border border-slate-200/70 bg-[#fbfcff] px-3 py-2"
                              >
                                <p className="text-[11px] text-slate-400">
                                  {metric.label}
                                </p>
                                <p className="mt-0.5 text-sm font-medium tabular-nums text-slate-900">
                                  {metric.value}
                                </p>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {summary.assumptions.length > 0 && (
                        <div>
                          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                            Assumptions
                          </h3>
                          <ul className="grid gap-2 sm:grid-cols-2">
                            {summary.assumptions.map((row) => (
                              <li
                                key={row.label}
                                className="flex items-center justify-between rounded-xl border border-violet-100 bg-violet-50/40 px-3 py-2 text-xs"
                              >
                                <span className="text-slate-600">{row.label}</span>
                                <span className="font-medium tabular-nums text-violet-700">
                                  {row.value}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {summary.projected.length > 0 && (
                        <div>
                          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                            Projected
                          </h3>
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-400">
                                <th className="pb-2 font-medium">Metric</th>
                                <th className="pb-2 pr-4 text-right font-medium">
                                  Observed
                                </th>
                                <th className="pb-2 pr-4 text-right font-medium">
                                  Projected
                                </th>
                                <th className="pb-2 text-right font-medium">
                                  Delta
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {summary.projected.map((metric) => (
                                <tr
                                  key={metric.label}
                                  className="border-b border-slate-100"
                                >
                                  <td className="py-2.5 text-xs font-medium text-slate-700">
                                    {metric.label}
                                  </td>
                                  <td className="py-2.5 pr-4 text-right tabular-nums text-slate-500">
                                    {metric.observed}
                                  </td>
                                  <td className="py-2.5 pr-4 text-right tabular-nums font-medium text-slate-900">
                                    {metric.projected}
                                  </td>
                                  <td className="py-2.5 text-right tabular-nums text-xs">
                                    {metric.delta}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}

                      {summary.value && (
                        <div>
                          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                            Value
                          </h3>
                          <div className="space-y-1.5">
                            {summary.value.map((row) => (
                              <div
                                key={row.label}
                                className="flex items-center justify-between text-xs"
                              >
                                <span className="text-slate-500">{row.label}</span>
                                <span className="font-medium tabular-nums text-slate-900">
                                  {row.value}
                                </span>
                              </div>
                            ))}
                          </div>
                          {summary.roiLabel && (
                            <div className="mt-2 flex items-center justify-between rounded-xl border border-violet-200 bg-violet-50/60 px-3 py-2.5 text-xs">
                              <span className="font-medium text-violet-700">
                                Projected ROI
                              </span>
                              <span className="font-semibold tabular-nums text-violet-800">
                                {summary.roiLabel}
                              </span>
                            </div>
                          )}
                          {summary.measurementStatusLabel && !summary.roiLabel && (
                            <p className="mt-2 text-[11px] leading-5 text-slate-400">
                              {summary.measurementStatusLabel} —{" "}
                              {ROI_UNAVAILABLE_MESSAGE}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  </Panel>
                </div>
              )}
            </>
          )}

          {actionError && (
            <div className="no-print mt-6 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
              {actionError}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}