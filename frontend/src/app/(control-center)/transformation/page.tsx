"use client";

import {
  Activity,
  ArrowRightLeft,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Coins,
  DollarSign,
  Layers,
  Lightbulb,
  MessageSquare,
  RefreshCw,
  ShieldAlert,
  Ticket,
  Timer,
  TrendingUp,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  TRANSFORMATION_DEFAULT_DAYS,
  TRANSFORMATION_WINDOW_DAYS,
  comparisonKeyLabel,
  fetchServiceTransformation,
  formatMinutes,
  formatMoney,
  formatPercentChange,
  formatRate,
  scopeLabel,
  signalLabel,
  type ServiceTransformationSummary,
} from "@/lib/transformation/transformation";

type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "violet";

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

function MetricCard({
  title,
  value,
  subtitle,
  icon: Icon,
  tone = "violet",
}: {
  title: string;
  value: string;
  subtitle: string;
  icon: typeof Ticket;
  tone?: "violet" | "blue" | "emerald" | "amber";
}) {
  const tones = {
    violet: "bg-violet-50 text-violet-500",
    blue: "bg-blue-50 text-blue-500",
    emerald: "bg-emerald-50 text-emerald-500",
    amber: "bg-amber-50 text-amber-500",
  }[tone];

  return (
    <div className="app-panel rounded-[20px] p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
            {title}
          </p>

          <p className="editorial-number mt-3 text-3xl font-medium tracking-[-0.045em] text-slate-950">
            {value}
          </p>
        </div>

        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${tones}`}
        >
          <Icon className="h-5 w-5" />
        </div>
      </div>

      <p className="mt-4 text-xs leading-5 text-slate-500">{subtitle}</p>
    </div>
  );
}

function ProgressRow({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  const safeValue = value === null || value === undefined ? 0 : value;
  const width = Math.max(0, Math.min(100, safeValue));

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-4">
        <span className="text-sm text-slate-500">{label}</span>

        <span className="text-sm font-medium text-slate-800">
          {formatRate(value)}
        </span>
      </div>

      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-gradient-to-r from-violet-500 to-blue-500 transition-all duration-500"
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

function SmallValue({
  label,
  value,
  valueClassName = "",
}: {
  label: string;
  value: React.ReactNode;
  valueClassName?: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
        {label}
      </p>

      <p
        className={`mt-2 text-xl font-medium tracking-[-0.025em] text-slate-900 ${valueClassName}`}
      >
        {value}
      </p>
    </div>
  );
}

function SectionHeading({
  icon: Icon,
  title,
  subtitle,
  tone = "violet",
}: {
  icon: typeof Ticket;
  title: string;
  subtitle: string;
  tone?: "violet" | "blue" | "emerald" | "amber";
}) {
  const tones = {
    violet: "bg-violet-50 text-violet-500",
    blue: "bg-blue-50 text-blue-500",
    emerald: "bg-emerald-50 text-emerald-500",
    amber: "bg-amber-50 text-amber-500",
  }[tone];

  return (
    <div className="mb-4 flex items-center gap-3">
      <div className={`flex h-9 w-9 items-center justify-center rounded-xl ${tones}`}>
        <Icon className="h-4 w-4" />
      </div>

      <div>
        <h2 className="font-medium text-slate-900">{title}</h2>
        <p className="text-xs text-slate-400">{subtitle}</p>
      </div>
    </div>
  );
}

export default function TransformationPage() {
  const [days, setDays] = useState<number>(TRANSFORMATION_DEFAULT_DAYS);
  const [summary, setSummary] = useState<ServiceTransformationSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const loadData = useCallback(
    async (windowDays: number) => {
      setLoading(true);
      setError("");

      try {
        const data = await fetchServiceTransformation(windowDays);
        setSummary(data);
        setLastUpdated(new Date());
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Could not load transformation data.",
        );
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadData(days);
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [days, loadData]);

  const sampleProgress = useMemo(() => {
    if (!summary) return 0;
    if (summary.value_realization.minimum_autonomous_samples <= 0) return 100;
    return Math.min(
      100,
      (summary.ai_adoption.autonomous_executions /
        summary.value_realization.minimum_autonomous_samples) *
        100,
    );
  }, [summary]);

  const comparisonEntries = useMemo(() => {
    if (!summary) return [];
    return Object.entries(summary.comparisons)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, comparison]) => ({ key, comparison }));
  }, [summary]);

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Service Transformation
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Production service value analytics
              </p>
            </div>

            <div className="flex items-center gap-3">
              {lastUpdated && (
                <span className="hidden text-[11px] text-slate-400 sm:block">
                  Updated {lastUpdated.toLocaleTimeString()}
                </span>
              )}

              <div className="flex items-center gap-1 rounded-full border border-slate-200 bg-white/80 p-1 text-[11px] text-slate-500 shadow-sm">
                {TRANSFORMATION_WINDOW_DAYS.map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setDays(option)}
                    className={`rounded-full px-3 py-1.5 font-medium transition ${
                      days === option
                        ? "bg-violet-500 text-white shadow-sm"
                        : "text-slate-500 hover:text-slate-800"
                    }`}
                  >
                    {option}D
                  </button>
                ))}
              </div>

              <button
                type="button"
                onClick={() => void loadData(days)}
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh transformation data"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Service Transformation
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              From ticket volume to
              <span className="gradient-text"> measurable value.</span>
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Current-window service volume, SLA health, AI adoption,
              specialist usage, human workload and value realization, compared
              against the previous period of equal length.
            </p>
          </section>

          {error && (
            <div className="mb-6 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
              {error}
            </div>
          )}

          {loading && !summary ? (
            <div className="app-panel relative flex min-h-[60vh] overflow-hidden rounded-[22px]">
              <div className="soft-grid absolute inset-0 opacity-20" />

              <div className="relative m-auto text-center">
                <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[22px] bg-white shadow-[0_20px_55px_rgba(98,82,255,0.14)]">
                  <RefreshCw className="h-7 w-7 animate-spin text-violet-500" />
                </div>

                <p className="mt-5 text-sm font-medium text-slate-700">
                  Loading service transformation data
                </p>
              </div>
            </div>
          ) : (
            <>
              <section>
                <SectionHeading
                  icon={Ticket}
                  title="Service Volume & Outcomes"
                  subtitle={`Window: ${summary?.window.days ?? days} days, current vs previous period`}
                />

                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <MetricCard
                    title="Tickets Created"
                    value={`${summary?.service_volume.tickets_created ?? 0}`}
                    subtitle="Tickets created in the current window."
                    icon={Ticket}
                    tone="violet"
                  />

                  <MetricCard
                    title="Tickets Resolved"
                    value={`${summary?.service_volume.tickets_resolved ?? 0}`}
                    subtitle="Tickets resolved in the current window."
                    icon={CheckCircle2}
                    tone="emerald"
                  />

                  <MetricCard
                    title="Currently Open"
                    value={`${summary?.service_volume.currently_open ?? 0}`}
                    subtitle="All open tickets across the organization."
                    icon={Activity}
                    tone="blue"
                  />

                  <MetricCard
                    title="Needs Response"
                    value={`${summary?.service_volume.currently_needs_response ?? 0}`}
                    subtitle="Open tickets awaiting a human reply."
                    icon={MessageSquare}
                    tone="amber"
                  />
                </div>
              </section>

              <section className="app-panel mt-6 rounded-[22px] p-6 md:p-7">
                <SectionHeading
                  icon={Timer}
                  title="Service Performance"
                  subtitle="Responsiveness and resolution inside the current window"
                  tone="blue"
                />

                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  <SmallValue
                    label="Avg first response"
                    value={formatMinutes(
                      summary?.service_performance.average_first_response_minutes,
                    )}
                  />

                  <SmallValue
                    label="Median first response"
                    value={formatMinutes(
                      summary?.service_performance.median_first_response_minutes,
                    )}
                  />

                  <SmallValue
                    label="Avg resolution time"
                    value={formatMinutes(
                      summary?.service_performance.average_resolution_time_minutes,
                    )}
                  />

                  <SmallValue
                    label="Median resolution time"
                    value={formatMinutes(
                      summary?.service_performance.median_resolution_time_minutes,
                    )}
                  />
                </div>
              </section>

              <section className="mt-6 grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
                <div className="app-panel rounded-[22px] p-6 md:p-7">
                  <SectionHeading
                    icon={ShieldAlert}
                    title="SLA Health"
                    subtitle="Breaches, due-soon exposure, escalations and reopens"
                    tone="amber"
                  />

                  <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-3">
                    <SmallValue
                      label="First response breaches"
                      value={summary?.sla.first_response_sla_breaches ?? 0}
                      valueClassName="text-rose-600"
                    />

                    <SmallValue
                      label="Resolution breaches"
                      value={summary?.sla.resolution_sla_breaches ?? 0}
                      valueClassName="text-rose-600"
                    />

                    <SmallValue
                      label="Total breaches"
                      value={summary?.sla.total_sla_breaches ?? 0}
                      valueClassName="text-rose-600"
                    />

                    <SmallValue
                      label="Due soon"
                      value={summary?.sla.due_soon ?? 0}
                      valueClassName="text-amber-600"
                    />

                    <SmallValue
                      label="Escalations"
                      value={summary?.sla.escalation_count ?? 0}
                    />

                    <SmallValue
                      label="Reopened tickets"
                      value={summary?.sla.reopened_tickets ?? 0}
                    />
                  </div>

                  <div className="mt-6 grid gap-x-8 gap-y-6 md:grid-cols-2">
                    <ProgressRow
                      label="Escalation rate"
                      value={summary?.sla.escalation_rate ?? null}
                    />

                    <ProgressRow
                      label="Reopen rate"
                      value={summary?.sla.reopen_rate ?? null}
                    />
                  </div>
                </div>

                <div className="app-panel rounded-[22px] p-6 md:p-7">
                  <SectionHeading
                    icon={Lightbulb}
                    title="Opportunity Signals"
                    subtitle="Rule-driven focus areas from current-window data"
                    tone="blue"
                  />

                  {summary && summary.opportunity_signals.length === 0 ? (
                    <p className="text-sm text-slate-400">
                      No actionable signals in this window.
                    </p>
                  ) : (
                    <div className="space-y-4">
                      {summary?.opportunity_signals.map((signal, index) => (
                        <div
                          key={`${signal.signal}-${signal.scope_key}-${index}`}
                          className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4"
                        >
                          <div className="flex items-center justify-between gap-3">
                            <Badge variant="info">
                              {signalLabel(signal.signal)}
                            </Badge>

                            <span className="text-[11px] uppercase tracking-[0.12em] text-slate-400">
                              {scopeLabel(signal.scope_type)} ·{" "}
                              {signal.scope_key}
                            </span>
                          </div>

                          <div className="mt-3 flex flex-wrap gap-2">
                            {Object.entries(signal.evidence).map(
                              ([key, value]) => (
                                <Badge key={key}>
                                  {key.replaceAll("_", " ")} · {value}
                                </Badge>
                              ),
                            )}
                          </div>

                          <p className="mt-3 text-xs leading-5 text-slate-500">
                            {signal.suggested_focus}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </section>

              <section className="mt-6 grid gap-6 xl:grid-cols-3">
                <div className="app-panel rounded-[22px] p-6 xl:col-span-2">
                  <SectionHeading
                    icon={Bot}
                    title="AI Adoption"
                    subtitle="Analysis, autonomy, approvals and execution in the window"
                  />

                  <div className="grid gap-3 sm:grid-cols-3">
                    <SmallValue
                      label="Tickets analyzed by AI"
                      value={summary?.ai_adoption.tickets_analyzed_by_ai ?? 0}
                    />

                    <SmallValue
                      label="Agent runs"
                      value={summary?.ai_adoption.agent_runs ?? 0}
                    />

                    <SmallValue
                      label="Autonomous executions"
                      value={summary?.ai_adoption.autonomous_executions ?? 0}
                      valueClassName="text-emerald-600"
                    />

                    <SmallValue
                      label="Approval required"
                      value={summary?.ai_adoption.human_approval_required ?? 0}
                      valueClassName="text-amber-600"
                    />

                    <SmallValue
                      label="Human approved"
                      value={summary?.ai_adoption.human_approved ?? 0}
                    />

                    <SmallValue
                      label="Human rejected"
                      value={summary?.ai_adoption.human_rejected ?? 0}
                    />
                  </div>

                  <div className="mt-6 grid gap-x-8 gap-y-6 md:grid-cols-2">
                    <ProgressRow
                      label="AI analysis rate"
                      value={summary?.ai_adoption.ai_analysis_rate ?? null}
                    />

                    <ProgressRow
                      label="Autonomous execution rate"
                      value={summary?.ai_adoption.autonomous_execution_rate ?? null}
                    />

                    <ProgressRow
                      label="Human approval rate"
                      value={summary?.ai_adoption.human_approval_rate ?? null}
                    />

                    <ProgressRow
                      label="Execution success rate"
                      value={summary?.ai_adoption.execution_success_rate ?? null}
                    />
                  </div>

                  <div className="mt-6 grid gap-3 sm:grid-cols-3">
                    <SmallValue
                      label="Successful executions"
                      value={summary?.ai_adoption.successful_agent_executions ?? 0}
                      valueClassName="text-emerald-600"
                    />

                    <SmallValue
                      label="Failed executions"
                      value={summary?.ai_adoption.failed_agent_executions ?? 0}
                      valueClassName="text-rose-600"
                    />

                    <SmallValue
                      label="No-action runs"
                      value={summary?.ai_adoption.no_action_runs ?? 0}
                    />
                  </div>
                </div>

                <div className="app-panel rounded-[22px] p-6">
                  <SectionHeading
                    icon={BrainCircuit}
                    title="Specialist Usage"
                    subtitle="Coordinator, knowledge and action routing"
                    tone="violet"
                  />

                  <div className="grid gap-3 sm:grid-cols-2">
                    <SmallValue
                      label="Coordinator runs"
                      value={summary?.specialist_usage.coordinator_runs ?? 0}
                    />

                    <SmallValue
                      label="Knowledge runs"
                      value={summary?.specialist_usage.knowledge_specialist_runs ?? 0}
                    />

                    <SmallValue
                      label="Action runs"
                      value={summary?.specialist_usage.action_specialist_runs ?? 0}
                    />

                    <SmallValue
                      label="Invalid paths"
                      value={summary?.specialist_usage.invalid_specialist_path_count ?? 0}
                      valueClassName={
                        (summary?.specialist_usage.invalid_specialist_path_count ?? 0) > 0
                          ? "text-rose-600"
                          : ""
                      }
                    />
                  </div>

                  <div className="mt-6 space-y-5">
                    <ProgressRow
                      label="Knowledge usage rate"
                      value={summary?.specialist_usage.knowledge_usage_rate ?? null}
                    />

                    <ProgressRow
                      label="Pure action route rate"
                      value={summary?.specialist_usage.pure_action_route_rate ?? null}
                    />
                  </div>

                  <div className="mt-6 rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
                    <div className="flex justify-between gap-4">
                      <span className="text-sm text-slate-500">
                        Human messages sent
                      </span>

                      <span className="font-medium text-slate-900">
                        {summary?.human_workload.human_messages_sent ?? 0}
                      </span>
                    </div>

                    <div className="mt-3 flex justify-between gap-4">
                      <span className="text-sm text-slate-500">
                        AI-executed replies
                      </span>

                      <span className="font-medium text-slate-900">
                        {summary?.human_workload.ai_executed_replies ?? 0}
                      </span>
                    </div>
                  </div>
                </div>
              </section>

              <section className="app-panel mt-6 overflow-hidden rounded-[22px]">
                <div className="border-b border-slate-200/70 bg-gradient-to-r from-emerald-50/65 via-white to-violet-50/45 p-6 md:p-7">
                  <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-start">
                    <div>
                      <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
                          <DollarSign className="h-5 w-5" />
                        </div>

                        <div>
                          <h2 className="font-medium text-slate-950">
                            Automation Value & ROI
                          </h2>

                          <p className="text-xs text-slate-400">
                            Estimated operational value from measured
                            autonomous execution
                          </p>
                        </div>
                      </div>
                    </div>

                    <Badge
                      variant={
                        summary?.value_realization.sample_size_sufficient
                          ? "success"
                          : "warning"
                      }
                    >
                      {summary?.value_realization.sample_size_sufficient
                        ? "ROI measurement ready"
                        : "Insufficient sample"}
                    </Badge>
                  </div>
                </div>

                <div className="p-6 md:p-7">
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                    <SmallValue
                      label="Time saved"
                      value={`${summary?.value_realization.estimated_minutes_saved ?? 0} min`}
                    />

                    <SmallValue
                      label="Hours saved"
                      value={`${summary?.value_realization.estimated_hours_saved ?? 0}`}
                    />

                    <SmallValue
                      label="Labor value"
                      value={formatMoney(
                        summary?.value_realization.estimated_labor_savings_usd ?? 0,
                      )}
                    />

                    <SmallValue
                      label="Agent AI cost"
                      value={formatMoney(
                        summary?.value_realization.agent_ai_cost_usd ?? 0,
                      )}
                    />

                    <SmallValue
                      label="Net estimated value"
                      value={formatMoney(
                        summary?.value_realization.estimated_net_savings_usd ?? 0,
                      )}
                      valueClassName="text-emerald-600"
                    />

                    <SmallValue
                      label="Pricing"
                      value={
                        summary?.value_realization.pricing_configured
                          ? "Configured"
                          : "Not configured"
                      }
                    />
                  </div>

                  <div className="mt-6 grid gap-5 xl:grid-cols-[1.3fr_0.7fr]">
                    <div className="rounded-[20px] border border-amber-200 bg-amber-50/55 p-5">
                      <div className="flex gap-3">
                        <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />

                        <div className="w-full">
                          <div className="flex flex-col justify-between gap-3 sm:flex-row">
                            <div>
                              <p className="text-sm font-medium text-amber-800">
                                ROI sample-size guardrail
                              </p>

                              <p className="mt-1 max-w-2xl text-xs leading-5 text-amber-700/80">
                                Formal ROI remains withheld until enough
                                autonomous executions have been measured.
                              </p>
                            </div>

                            <span className="shrink-0 text-sm font-medium text-amber-800">
                              {summary?.ai_adoption.autonomous_executions ?? 0}/
                              {summary?.value_realization.minimum_autonomous_samples ?? 0}
                            </span>
                          </div>

                          <div className="mt-5 h-2.5 overflow-hidden rounded-full bg-amber-100">
                            <div
                              className="h-full rounded-full bg-gradient-to-r from-amber-400 to-orange-400 transition-all"
                              style={{ width: `${sampleProgress}%` }}
                            />
                          </div>

                          <p className="mt-4 text-xs text-amber-700">
                            Formal ROI:{" "}
                            {summary?.value_realization.roi_percent !== null &&
                            summary?.value_realization.roi_percent !== undefined
                              ? formatRate(summary.value_realization.roi_percent)
                              : "not yet reported"}
                          </p>
                        </div>
                      </div>
                    </div>

                    <div className="rounded-[20px] border border-slate-200 bg-[#fbfcff] p-5">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                        Measurement status
                      </p>

                      <p className="mt-2 font-medium text-slate-900">
                        {summary?.value_realization.measurement_status
                          ? summary.value_realization.measurement_status
                              .replaceAll("_", " ")
                          : "—"}
                      </p>

                      <p className="mt-5 text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                        Minimum autonomous samples
                      </p>

                      <p className="mt-2 font-medium text-slate-900">
                        {summary?.value_realization.minimum_autonomous_samples ?? 0}
                      </p>
                    </div>
                  </div>
                </div>
              </section>

              <section className="app-panel mt-6 rounded-[22px] p-6 md:p-7">
                <SectionHeading
                  icon={TrendingUp}
                  title="Window Comparison"
                  subtitle="Current window vs the previous period of equal length"
                  tone="emerald"
                />

                {comparisonEntries.length === 0 ? (
                  <p className="text-sm text-slate-400">No comparisons available.</p>
                ) : (
                  <div className="divide-y divide-slate-200/70">
                    {comparisonEntries.map(({ key, comparison }) => (
                      <div
                        key={key}
                        className="grid gap-2 py-3 sm:grid-cols-[1.6fr_1fr_1fr_1fr_1fr] sm:items-center"
                      >
                        <span className="text-sm font-medium text-slate-800">
                          {comparisonKeyLabel(key)}
                        </span>

                        <span className="text-sm text-slate-500">
                          Current:{" "}
                          <span className="text-slate-800">
                            {comparison.current ?? "—"}
                          </span>
                        </span>

                        <span className="text-sm text-slate-500">
                          Previous:{" "}
                          <span className="text-slate-800">
                            {comparison.previous ?? "—"}
                          </span>
                        </span>

                        <span className="text-sm text-slate-500">
                          Change:{" "}
                          <span className="text-slate-800">
                            {comparison.absolute_change ?? "—"}
                          </span>
                        </span>

                        <span
                          className={`text-sm font-medium ${
                            comparison.percent_change !== null &&
                            comparison.percent_change !== undefined &&
                            comparison.percent_change < 0
                              ? "text-emerald-600"
                              : "text-slate-800"
                          }`}
                        >
                          {formatPercentChange(comparison.percent_change)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="app-panel mt-6 rounded-[22px] p-6 md:p-7">
                <SectionHeading
                  icon={Layers}
                  title="Queue Breakdown"
                  subtitle="Current-state workload, SLA exposure and window agent runs by queue"
                  tone="blue"
                />

                {summary && summary.queue_breakdown.length === 0 ? (
                  <p className="text-sm text-slate-400">No queue data available.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-200/70 text-left text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                          <th className="py-3 pr-4">Queue</th>
                          <th className="py-3 pr-4">Open</th>
                          <th className="py-3 pr-4">Needs response</th>
                          <th className="py-3 pr-4">Due soon</th>
                          <th className="py-3 pr-4">Breached</th>
                          <th className="py-3 pr-4">Urgent/High</th>
                          <th className="py-3 pr-4">Assigned</th>
                          <th className="py-3 pr-4">Resolved</th>
                          <th className="py-3 pr-4">Agent runs</th>
                          <th className="py-3">Autonomous</th>
                        </tr>
                      </thead>

                      <tbody>
                        {summary?.queue_breakdown.map((queue) => (
                          <tr
                            key={queue.queue_key ?? queue.queue_name ?? "queue"}
                            className="border-b border-slate-200/60 last:border-0"
                          >
                            <td className="py-3 pr-4 font-medium text-slate-800">
                              {queue.queue_name ?? queue.queue_key ?? "Unnamed"}
                            </td>
                            <td className="py-3 pr-4 text-slate-600">
                              {queue.open_tickets}
                            </td>
                            <td className="py-3 pr-4 text-slate-600">
                              {queue.needs_response}
                            </td>
                            <td className="py-3 pr-4 text-amber-600">
                              {queue.due_soon}
                            </td>
                            <td className="py-3 pr-4 text-rose-600">
                              {queue.breached}
                            </td>
                            <td className="py-3 pr-4 text-slate-600">
                              {queue.priority_urgent_high}
                            </td>
                            <td className="py-3 pr-4 text-slate-600">
                              {queue.assigned_tickets}
                            </td>
                            <td className="py-3 pr-4 text-slate-600">
                              {queue.resolved_in_window}
                            </td>
                            <td className="py-3 pr-4 text-slate-600">
                              {queue.agent_runs}
                            </td>
                            <td className="py-3 text-emerald-600">
                              {queue.autonomous_executions}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="app-panel mt-6 rounded-[22px] p-6 md:p-7">
                <SectionHeading
                  icon={ArrowRightLeft}
                  title="Channel Breakdown"
                  subtitle="Conversation and message volume by support channel"
                  tone="violet"
                />

                {summary && summary.channel_breakdown.length === 0 ? (
                  <p className="text-sm text-slate-400">No channel data available.</p>
                ) : (
                  <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                    {summary?.channel_breakdown.map((channel) => (
                      <div
                        key={channel.channel}
                        className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-medium text-slate-800">
                            {channel.channel}
                          </span>

                          <Badge variant="info">
                            {formatRate(channel.percentage)}
                          </Badge>
                        </div>

                        <div className="mt-4 space-y-2">
                          <div className="flex justify-between gap-4 text-xs text-slate-500">
                            <span>Conversations</span>
                            <span className="font-medium text-slate-800">
                              {channel.conversation_count}
                            </span>
                          </div>

                          <div className="flex justify-between gap-4 text-xs text-slate-500">
                            <span>Messages</span>
                            <span className="font-medium text-slate-800">
                              {channel.message_count}
                            </span>
                          </div>
                        </div>

                        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-violet-500 to-blue-500"
                            style={{
                              width: `${Math.max(
                                0,
                                Math.min(100, channel.percentage),
                              )}%`,
                            }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="app-panel mt-6 rounded-[22px] p-6 md:p-7">
                <div className="flex flex-col justify-between gap-5 md:flex-row md:items-center">
                  <div>
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                        <Coins className="h-5 w-5" />
                      </div>

                      <div>
                        <h2 className="font-medium text-slate-900">
                          Comparison Window
                        </h2>

                        <p className="text-xs text-slate-400">
                          Current vs previous period boundaries
                        </p>
                      </div>
                    </div>

                    <p className="mt-5 max-w-3xl text-sm leading-7 text-slate-500">
                      {" "}
                      {summary?.window.days ?? days}-day window ending{" "}
                      {summary
                        ? new Date(summary.window.current_end).toLocaleDateString(
                            undefined,
                            { month: "short", day: "numeric", year: "numeric" },
                          )
                        : ""}
                      , compared against the prior{" "}
                      {summary?.window.days ?? days} days.
                    </p>
                  </div>

                  <Badge variant="success">{days}D window</Badge>
                </div>
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}