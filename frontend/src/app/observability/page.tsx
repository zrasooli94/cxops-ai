"use client";

import {
  Activity,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  Coins,
  Database,
  DollarSign,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  TriangleAlert,
  XCircle,
  Zap,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import AppSidebar from "@/components/app-sidebar";

type AIObservabilitySummary = {
  total_requests: number;
  success_rate: number;
  grounded_rate: number;
  avg_latency_ms: number;
  total_tokens: number;
  estimated_cost_usd: number;
};

type AIFeatureSummary = {
  feature: string;
  total_requests: number;
  success_rate: number;
  grounded_rate: number;
  llm_call_rate: number;
  avg_latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  models: Record<string, number>;
};

type AIBreakdown = {
  overall: AIObservabilitySummary;
  features: AIFeatureSummary[];
};

type IntegrationJobSummary = {
  total: number;
  statuses: Record<string, number>;
  job_types: Record<string, number>;
  total_attempts: number;
  retried_jobs: number;
  retry_rate: number;
  completed_jobs: number;
  failed_jobs: number;
  exhausted_jobs: number;
};

type AgentSummary = {
  generated_at: string;
  total_runs: number;
  actions: Record<string, number>;
  statuses: Record<string, number>;
  human_approval_required: number;
  human_approval_rate: number;
  reviewed_runs: number;
  review_rate: number;
  executed_runs: number;
  execution_failed_runs: number;
  execution_success_rate: number;
  auto_execution_eligible_runs: number;
  auto_execution_eligible_rate: number;
  tool_usage: Record<string, number>;
  tool_risk_levels: Record<string, number>;
  approval_gated_tools: number;
  authorized_tools: number;
  integration_jobs: IntegrationJobSummary;
};

type AgentKPIs = {
  total_runs: number;
  escalation_rate: number;
  human_review_rate: number;
  no_action_rate: number;
  approval_required_rate: number;
  pending_approval_rate: number;
  auto_approved_runs: number;
  autonomous_execution_rate: number;
  autonomous_executed_runs: number;
  autonomous_success_rate: number;
  execution_success_rate: number;
  queue_retry_rate: number;
  queue_failure_rate: number;
  average_job_attempts: number;
};

type AgentROI = {
  total_runs: number;
  instrumented_runs: number;
  instrumented_autonomous_executed_runs: number;
  support_hourly_cost_usd: number;
  minutes_saved_per_autonomous_execution: number;
  estimated_minutes_saved: number;
  estimated_hours_saved: number;
  estimated_labor_savings_usd: number;
  agent_ai_cost_usd: number;
  estimated_net_savings_usd: number;
  pricing_configured: boolean;
  measurement_status: string;
  minimum_autonomous_samples: number;
  sample_size_sufficient: boolean;
  provisional_roi_percent: number | null;
  roi_percent: number | null;
};

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
    default:
      "border-slate-200 bg-slate-50 text-slate-600",
    success:
      "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning:
      "border-amber-200 bg-amber-50 text-amber-700",
    danger:
      "border-rose-200 bg-rose-50 text-rose-700",
    info:
      "border-blue-200 bg-blue-50 text-blue-700",
    violet:
      "border-violet-200 bg-violet-50 text-violet-700",
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
  icon: typeof Activity;
  tone?: "violet" | "blue" | "emerald" | "amber";
}) {
  const tones = {
    violet:
      "bg-violet-50 text-violet-500",
    blue: "bg-blue-50 text-blue-500",
    emerald:
      "bg-emerald-50 text-emerald-500",
    amber:
      "bg-amber-50 text-amber-500",
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

      <p className="mt-4 text-xs leading-5 text-slate-500">
        {subtitle}
      </p>
    </div>
  );
}

function ProgressRow({
  label,
  value,
  suffix = "%",
}: {
  label: string;
  value: number;
  suffix?: string;
}) {
  const safeValue =
    Number.isFinite(value) ? value : 0;

  const width = Math.max(
    0,
    Math.min(100, safeValue),
  );

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-4">
        <span className="text-sm text-slate-500">
          {label}
        </span>

        <span className="text-sm font-medium text-slate-800">
          {safeValue.toFixed(2)}
          {suffix}
        </span>
      </div>

      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-gradient-to-r from-violet-500 to-blue-500 transition-all duration-500"
          style={{
            width: `${width}%`,
          }}
        />
      </div>
    </div>
  );
}

function formatKey(value: string) {
  return value
    .replaceAll("_", " ")
    .split(" ")
    .map(
      (part) =>
        part.charAt(0).toUpperCase() +
        part.slice(1),
    )
    .join(" ");
}

function ratioToPercent(
  value: number,
) {
  return value * 100;
}

function KeyValueList({
  data,
}: {
  data: Record<string, number>;
}) {
  const entries =
    Object.entries(data);

  if (entries.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        No data available.
      </p>
    );
  }

  const max = Math.max(
    ...entries.map(
      ([, value]) => value,
    ),
    1,
  );

  return (
    <div className="space-y-4">
      {entries.map(
        ([key, value]) => (
          <div key={key}>
            <div className="flex justify-between gap-4">
              <span className="text-sm text-slate-500">
                {formatKey(key)}
              </span>

              <span className="text-sm font-medium text-slate-800">
                {value}
              </span>
            </div>

            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-gradient-to-r from-violet-500 to-blue-500"
                style={{
                  width: `${
                    (value / max) * 100
                  }%`,
                }}
              />
            </div>
          </div>
        ),
      )}
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

export default function ObservabilityPage() {
  const [ai, setAi] =
    useState<AIBreakdown | null>(
      null,
    );

  const [agent, setAgent] =
    useState<AgentSummary | null>(
      null,
    );

  const [kpis, setKpis] =
    useState<AgentKPIs | null>(
      null,
    );

  const [roi, setRoi] =
    useState<AgentROI | null>(
      null,
    );

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState("");

  const [
    lastUpdated,
    setLastUpdated,
  ] = useState<Date | null>(
    null,
  );

  const loadData =
    useCallback(async () => {
      setLoading(true);
      setError("");

      try {
        const [
          aiResponse,
          agentResponse,
          kpiResponse,
          roiResponse,
        ] = await Promise.all([
          fetch(
            "/api/backend/observability/ai/by-feature",
            {
              cache: "no-store",
            },
          ),
          fetch(
            "/api/backend/observability/agent/summary",
            {
              cache: "no-store",
            },
          ),
          fetch(
            "/api/backend/observability/agent/kpis",
            {
              cache: "no-store",
            },
          ),
          fetch(
            "/api/backend/observability/agent/roi",
            {
              cache: "no-store",
            },
          ),
        ]);

        if (
          !aiResponse.ok ||
          !agentResponse.ok ||
          !kpiResponse.ok ||
          !roiResponse.ok
        ) {
          throw new Error(
            "One or more observability endpoints failed.",
          );
        }

        const [
          aiData,
          agentData,
          kpiData,
          roiData,
        ] = await Promise.all([
          aiResponse.json(),
          agentResponse.json(),
          kpiResponse.json(),
          roiResponse.json(),
        ]);

        setAi(
          aiData as AIBreakdown,
        );

        setAgent(
          agentData as AgentSummary,
        );

        setKpis(
          kpiData as AgentKPIs,
        );

        setRoi(
          roiData as AgentROI,
        );

        setLastUpdated(
          new Date(),
        );
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load telemetry.",
        );
      } finally {
        setLoading(false);
      }
    }, []);

  useEffect(() => {
    const timer =
      window.setTimeout(() => {
        void loadData();
      }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [loadData]);

  const sampleProgress =
    useMemo(() => {
      if (!roi) {
        return 0;
      }

      if (
        roi.minimum_autonomous_samples <=
        0
      ) {
        return 100;
      }

      return Math.min(
        100,
        (roi.instrumented_autonomous_executed_runs /
          roi.minimum_autonomous_samples) *
          100,
      );
    }, [roi]);

  return (
    <div className="min-h-screen">
      <AppSidebar active="/observability" />

      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Observability
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Production telemetry
              </p>
            </div>

            <div className="flex items-center gap-3">
              {lastUpdated && (
                <span className="hidden text-[11px] text-slate-400 sm:block">
                  Updated{" "}
                  {lastUpdated.toLocaleTimeString()}
                </span>
              )}

              <div className="hidden items-center gap-2 rounded-full border border-slate-200 bg-white/80 px-4 py-2 text-[11px] text-slate-500 shadow-sm md:flex">
                <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]" />
                Telemetry active
              </div>

              <button
                type="button"
                onClick={() =>
                  void loadData()
                }
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh telemetry"
              >
                <RefreshCw
                  className={`h-4 w-4 ${
                    loading
                      ? "animate-spin"
                      : ""
                  }`}
                />
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Production Telemetry
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Observe the system,
              <span className="gradient-text">
                {" "}
                not just the model.
              </span>
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Measure AI quality, grounding,
              latency, cost, agent safety,
              queue reliability and estimated
              operational value from persisted
              production telemetry.
            </p>
          </section>

          {error && (
            <div className="mb-6 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
              {error}
            </div>
          )}

          {loading && !ai ? (
            <div className="app-panel relative flex min-h-[60vh] overflow-hidden rounded-[22px]">
              <div className="soft-grid absolute inset-0 opacity-20" />

              <div className="relative m-auto text-center">
                <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[22px] bg-white shadow-[0_20px_55px_rgba(98,82,255,0.14)]">
                  <RefreshCw className="h-7 w-7 animate-spin text-violet-500" />
                </div>

                <p className="mt-5 text-sm font-medium text-slate-700">
                  Loading production telemetry
                </p>
              </div>
            </div>
          ) : (
            <>
              <section>
                <div className="mb-4 flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                    <BrainCircuit className="h-4 w-4" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-900">
                      AI Performance
                    </h2>

                    <p className="text-xs text-slate-400">
                      Persisted model and RAG measurements
                    </p>
                  </div>
                </div>

                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
                  <MetricCard
                    title="AI Requests"
                    value={`${ai?.overall.total_requests ?? 0}`}
                    subtitle="Persisted production AI requests."
                    icon={Bot}
                    tone="violet"
                  />

                  <MetricCard
                    title="Success Rate"
                    value={`${ratioToPercent(
                      ai?.overall.success_rate ??
                        0,
                    ).toFixed(2)}%`}
                    subtitle="AI requests completed successfully."
                    icon={CheckCircle2}
                    tone="emerald"
                  />

                  <MetricCard
                    title="Grounded Rate"
                    value={`${ratioToPercent(
                      ai?.overall.grounded_rate ??
                        0,
                    ).toFixed(2)}%`}
                    subtitle="Responses recorded as grounded."
                    icon={ShieldCheck}
                    tone="blue"
                  />

                  <MetricCard
                    title="Avg Latency"
                    value={`${(
                      ai?.overall.avg_latency_ms ??
                      0
                    ).toFixed(0)} ms`}
                    subtitle="Average measured AI latency."
                    icon={Clock3}
                    tone="amber"
                  />

                  <MetricCard
                    title="Tokens"
                    value={(
                      ai?.overall.total_tokens ??
                      0
                    ).toLocaleString()}
                    subtitle="Total measured token usage."
                    icon={Zap}
                    tone="violet"
                  />

                  <MetricCard
                    title="AI Cost"
                    value={`$${(
                      ai?.overall
                        .estimated_cost_usd ??
                      0
                    ).toFixed(6)}`}
                    subtitle="Estimated configured model cost."
                    icon={Coins}
                    tone="emerald"
                  />
                </div>
              </section>

              <section className="mt-6 grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
                <div className="app-panel rounded-[22px] p-6 md:p-7">
                  <div>
                    <h2 className="font-medium text-slate-900">
                      Agent Operational KPIs
                    </h2>

                    <p className="mt-1 text-sm text-slate-500">
                      Safety, autonomy and
                      execution behavior across
                      persisted agent runs.
                    </p>
                  </div>

                  <div className="mt-7 grid gap-x-8 gap-y-6 md:grid-cols-2">
                    <ProgressRow
                      label="Escalation rate"
                      value={
                        kpis?.escalation_rate ??
                        0
                      }
                    />

                    <ProgressRow
                      label="Human review rate"
                      value={
                        kpis?.human_review_rate ??
                        0
                      }
                    />

                    <ProgressRow
                      label="Approval required"
                      value={
                        kpis?.approval_required_rate ??
                        0
                      }
                    />

                    <ProgressRow
                      label="Pending approval"
                      value={
                        kpis?.pending_approval_rate ??
                        0
                      }
                    />

                    <ProgressRow
                      label="Autonomous execution"
                      value={
                        kpis?.autonomous_execution_rate ??
                        0
                      }
                    />

                    <ProgressRow
                      label="Autonomous success"
                      value={
                        kpis?.autonomous_success_rate ??
                        0
                      }
                    />

                    <ProgressRow
                      label="No-action rate"
                      value={
                        kpis?.no_action_rate ??
                        0
                      }
                    />

                    <ProgressRow
                      label="Overall execution success"
                      value={
                        kpis?.execution_success_rate ??
                        0
                      }
                    />
                  </div>

                  <div className="mt-7 grid gap-3 sm:grid-cols-3">
                    <SmallValue
                      label="Total agent runs"
                      value={
                        kpis?.total_runs ??
                        agent?.total_runs ??
                        0
                      }
                    />

                    <SmallValue
                      label="Auto-approved"
                      value={
                        kpis?.auto_approved_runs ??
                        0
                      }
                    />

                    <SmallValue
                      label="Autonomous executed"
                      value={
                        kpis?.autonomous_executed_runs ??
                        0
                      }
                    />
                  </div>
                </div>

                <div className="app-panel rounded-[22px] p-6 md:p-7">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <h2 className="font-medium text-slate-900">
                        Durable Queue
                      </h2>

                      <p className="mt-1 text-sm leading-6 text-slate-500">
                        All-time queue history;
                        failed jobs are retained
                        for auditability.
                      </p>
                    </div>

                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                      <ServerCog className="h-5 w-5" />
                    </div>
                  </div>

                  <div className="mt-6 grid gap-3 sm:grid-cols-2">
                    <SmallValue
                      label="Total jobs"
                      value={
                        agent?.integration_jobs
                          .total ?? 0
                      }
                    />

                    <SmallValue
                      label="Completed"
                      value={
                        agent?.integration_jobs
                          .completed_jobs ?? 0
                      }
                      valueClassName="text-emerald-600"
                    />

                    <SmallValue
                      label="Retried jobs"
                      value={
                        agent?.integration_jobs
                          .retried_jobs ?? 0
                      }
                      valueClassName="text-amber-600"
                    />

                    <SmallValue
                      label="Historical failed jobs"
                      value={
                        agent?.integration_jobs
                          .failed_jobs ?? 0
                      }
                      valueClassName="text-rose-600"
                    />
                  </div>

                  <div className="mt-6 space-y-5">
                    <ProgressRow
                      label="All-time retry rate"
                      value={
                        kpis?.queue_retry_rate ??
                        0
                      }
                    />

                    <ProgressRow
                      label="All-time failure rate"
                      value={
                        kpis?.queue_failure_rate ??
                        0
                      }
                    />
                  </div>

                  <div className="mt-6 rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
                    <div className="flex justify-between gap-4">
                      <span className="text-sm text-slate-500">
                        Average attempts
                      </span>

                      <span className="font-medium text-slate-900">
                        {kpis?.average_job_attempts ??
                          0}
                      </span>
                    </div>

                    <div className="mt-3 flex justify-between gap-4">
                      <span className="text-sm text-slate-500">
                        Total attempts
                      </span>

                      <span className="font-medium text-slate-900">
                        {agent?.integration_jobs
                          .total_attempts ?? 0}
                      </span>
                    </div>

                    <div className="mt-3 flex justify-between gap-4">
                      <span className="text-sm text-slate-500">
                        Exhausted jobs
                      </span>

                      <span className="font-medium text-slate-900">
                        {agent?.integration_jobs
                          .exhausted_jobs ?? 0}
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
                            Estimated operational value
                          </p>
                        </div>
                      </div>
                    </div>

                    <Badge
                      variant={
                        roi?.sample_size_sufficient
                          ? "success"
                          : "warning"
                      }
                    >
                      {roi?.sample_size_sufficient
                        ? "ROI measurement ready"
                        : "Insufficient sample"}
                    </Badge>
                  </div>
                </div>

                <div className="p-6 md:p-7">
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                    <SmallValue
                      label="Instrumented runs"
                      value={
                        roi?.instrumented_runs ??
                        0
                      }
                    />

                    <SmallValue
                      label="Autonomous samples"
                      value={
                        roi?.instrumented_autonomous_executed_runs ??
                        0
                      }
                    />

                    <SmallValue
                      label="Time saved"
                      value={`${
                        roi?.estimated_minutes_saved ??
                        0
                      } min`}
                    />

                    <SmallValue
                      label="Labor value"
                      value={`$${(
                        roi?.estimated_labor_savings_usd ??
                        0
                      ).toFixed(2)}`}
                    />

                    <SmallValue
                      label="Agent AI cost"
                      value={`$${(
                        roi?.agent_ai_cost_usd ??
                        0
                      ).toFixed(6)}`}
                    />

                    <SmallValue
                      label="Net estimated value"
                      value={`$${(
                        roi?.estimated_net_savings_usd ??
                        0
                      ).toFixed(2)}`}
                      valueClassName="text-emerald-600"
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
                                Formal ROI remains
                                withheld until enough
                                autonomous executions
                                have been measured.
                              </p>
                            </div>

                            <span className="shrink-0 text-sm font-medium text-amber-800">
                              {roi?.instrumented_autonomous_executed_runs ??
                                0}
                              /
                              {roi?.minimum_autonomous_samples ??
                                0}
                            </span>
                          </div>

                          <div className="mt-5 h-2.5 overflow-hidden rounded-full bg-amber-100">
                            <div
                              className="h-full rounded-full bg-gradient-to-r from-amber-400 to-orange-400 transition-all"
                              style={{
                                width: `${sampleProgress}%`,
                              }}
                            />
                          </div>

                          <p className="mt-4 text-xs text-amber-700">
                            Formal ROI:{" "}
                            {roi?.roi_percent !==
                              null &&
                            roi?.roi_percent !==
                              undefined
                              ? `${roi.roi_percent.toFixed(
                                  2,
                                )}%`
                              : "not yet reported"}
                          </p>
                        </div>
                      </div>
                    </div>

                    <div className="rounded-[20px] border border-slate-200 bg-[#fbfcff] p-5">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                        Measurement assumptions
                      </p>

                      <div className="mt-4 space-y-4">
                        <div>
                          <p className="text-xs text-slate-400">
                            Support hourly cost
                          </p>

                          <p className="mt-1 font-medium text-slate-900">
                            $
                            {(
                              roi?.support_hourly_cost_usd ??
                              0
                            ).toFixed(2)}
                          </p>
                        </div>

                        <div>
                          <p className="text-xs text-slate-400">
                            Minutes saved per autonomous execution
                          </p>

                          <p className="mt-1 font-medium text-slate-900">
                            {roi?.minutes_saved_per_autonomous_execution ??
                              0}{" "}
                            min
                          </p>
                        </div>

                        <div>
                          <p className="text-xs text-slate-400">
                            Measurement status
                          </p>

                          <p className="mt-1 font-medium text-slate-900">
                            {roi?.measurement_status
                              ? formatKey(
                                  roi.measurement_status,
                                )
                              : "—"}
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </section>

              <section className="mt-6">
                <div className="mb-4 flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                    <Activity className="h-4 w-4" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-900">
                      AI Feature Breakdown
                    </h2>

                    <p className="text-xs text-slate-400">
                      Quality, latency, usage and cost by AI feature
                    </p>
                  </div>
                </div>

                <div className="grid gap-6 xl:grid-cols-2">
                  {ai?.features.map(
                    (feature) => (
                      <div
                        key={feature.feature}
                        className="app-panel rounded-[22px] p-6"
                      >
                        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                          <div>
                            <Badge variant="info">
                              {formatKey(
                                feature.feature,
                              )}
                            </Badge>

                            <p className="mt-3 text-sm text-slate-500">
                              {
                                feature.total_requests
                              }{" "}
                              requests
                            </p>
                          </div>

                          <div className="sm:text-right">
                            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">
                              Estimated cost
                            </p>

                            <p className="mt-1 font-medium text-slate-900">
                              $
                              {feature.estimated_cost_usd.toFixed(
                                6,
                              )}
                            </p>
                          </div>
                        </div>

                        <div className="mt-6 space-y-5">
                          <ProgressRow
                            label="Success"
                            value={ratioToPercent(
                              feature.success_rate,
                            )}
                          />

                          <ProgressRow
                            label="Grounded"
                            value={ratioToPercent(
                              feature.grounded_rate,
                            )}
                          />

                          <ProgressRow
                            label="LLM call rate"
                            value={ratioToPercent(
                              feature.llm_call_rate,
                            )}
                          />
                        </div>

                        <div className="mt-6 grid gap-3 sm:grid-cols-3">
                          <SmallValue
                            label="Latency"
                            value={`${feature.avg_latency_ms.toFixed(
                              0,
                            )} ms`}
                          />

                          <SmallValue
                            label="Input tokens"
                            value={feature.input_tokens.toLocaleString()}
                          />

                          <SmallValue
                            label="Output tokens"
                            value={feature.output_tokens.toLocaleString()}
                          />
                        </div>

                        <div className="mt-5 border-t border-slate-200/70 pt-5">
                          <p className="mb-3 text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                            Models
                          </p>

                          <div className="flex flex-wrap gap-2">
                            {Object.entries(
                              feature.models,
                            ).map(
                              ([
                                model,
                                count,
                              ]) => (
                                <Badge
                                  key={model}
                                >
                                  {model} ·{" "}
                                  {count}
                                </Badge>
                              ),
                            )}
                          </div>
                        </div>
                      </div>
                    ),
                  )}
                </div>
              </section>

              <section className="mt-6 grid gap-6 xl:grid-cols-3">
                <div className="app-panel rounded-[22px] p-6">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                      <Bot className="h-5 w-5" />
                    </div>

                    <div>
                      <h2 className="font-medium text-slate-900">
                        Agent Actions
                      </h2>

                      <p className="text-xs text-slate-400">
                        Decision distribution
                      </p>
                    </div>
                  </div>

                  <div className="mt-6">
                    <KeyValueList
                      data={
                        agent?.actions ?? {}
                      }
                    />
                  </div>
                </div>

                <div className="app-panel rounded-[22px] p-6">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                      <Database className="h-5 w-5" />
                    </div>

                    <div>
                      <h2 className="font-medium text-slate-900">
                        Agent Statuses
                      </h2>

                      <p className="text-xs text-slate-400">
                        Lifecycle state distribution
                      </p>
                    </div>
                  </div>

                  <div className="mt-6">
                    <KeyValueList
                      data={
                        agent?.statuses ?? {}
                      }
                    />
                  </div>
                </div>

                <div className="app-panel rounded-[22px] p-6">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-50 text-amber-500">
                      <ShieldCheck className="h-5 w-5" />
                    </div>

                    <div>
                      <h2 className="font-medium text-slate-900">
                        Tool Risk Levels
                      </h2>

                      <p className="text-xs text-slate-400">
                        Authorization exposure
                      </p>
                    </div>
                  </div>

                  <div className="mt-6">
                    <KeyValueList
                      data={
                        agent?.tool_risk_levels ??
                        {}
                      }
                    />
                  </div>
                </div>
              </section>

              <section className="app-panel mt-6 rounded-[22px] p-6 md:p-7">
                <div className="flex flex-col justify-between gap-5 md:flex-row md:items-center">
                  <div>
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
                        <ServerCog className="h-5 w-5" />
                      </div>

                      <div>
                        <h2 className="font-medium text-slate-900">
                          Production Measurement Architecture
                        </h2>

                        <p className="text-xs text-slate-400">
                          Persistent and live instrumentation
                        </p>
                      </div>
                    </div>

                    <p className="mt-5 max-w-3xl text-sm leading-7 text-slate-500">
                      PostgreSQL stores historical
                      AI and agent telemetry while
                      Prometheus-compatible metrics
                      expose live API and worker
                      process measurements.
                    </p>
                  </div>

                  <Badge variant="success">
                    Instrumented
                  </Badge>
                </div>

                <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                  {[
                    "AI Request Logs",
                    "Agent Runs",
                    "Audit Events",
                    "Queue Jobs",
                    "Prometheus",
                  ].map((item) => (
                    <div
                      key={item}
                      className="flex items-center gap-3 rounded-xl border border-slate-200/70 bg-[#fbfcff] px-4 py-3"
                    >
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />

                      <span className="text-sm text-slate-600">
                        {item}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
