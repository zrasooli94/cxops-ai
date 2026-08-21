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
  Gauge,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  Ticket,
  TriangleAlert,
  Workflow,
  XCircle,
  Zap,
} from "lucide-react";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

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

function SidebarItem({
  href,
  icon: Icon,
  label,
  active = false,
}: {
  href: string;
  icon: typeof Activity;
  label: string;
  active?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${
        active
          ? "bg-cyan-400/10 text-cyan-300"
          : "text-slate-400 hover:bg-slate-900 hover:text-white"
      }`}
    >
      <Icon className="h-4 w-4" />
      {label}
    </Link>
  );
}

function Badge({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?:
    | "default"
    | "success"
    | "warning"
    | "danger"
    | "info";
}) {
  const styles = {
    default: "bg-slate-800 text-slate-300",
    success:
      "bg-emerald-400/10 text-emerald-300",
    warning:
      "bg-amber-400/10 text-amber-300",
    danger:
      "bg-red-400/10 text-red-300",
    info:
      "bg-cyan-400/10 text-cyan-300",
  };

  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${styles[variant]}`}
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
}: {
  title: string;
  value: string;
  subtitle: string;
  icon: typeof Activity;
}) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-slate-500">
            {title}
          </p>

          <p className="mt-2 text-3xl font-semibold tracking-tight">
            {value}
          </p>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <Icon className="h-5 w-5 text-cyan-400" />
        </div>
      </div>

      <p className="mt-3 text-xs leading-5 text-slate-600">
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
  const width = Math.max(
    0,
    Math.min(100, value),
  );

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-4">
        <span className="text-sm text-slate-400">
          {label}
        </span>

        <span className="text-sm font-medium">
          {value.toFixed(2)}
          {suffix}
        </span>
      </div>

      <div className="h-2 overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full rounded-full bg-cyan-400 transition-all"
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

function ratioToPercent(value: number) {
  return value * 100;
}

function KeyValueList({
  data,
}: {
  data: Record<string, number>;
}) {
  const entries = Object.entries(data);

  if (entries.length === 0) {
    return (
      <p className="text-sm text-slate-600">
        No data available.
      </p>
    );
  }

  const max = Math.max(
    ...entries.map(([, value]) => value),
    1,
  );

  return (
    <div className="space-y-4">
      {entries.map(([key, value]) => (
        <div key={key}>
          <div className="flex justify-between gap-4">
            <span className="text-sm text-slate-400">
              {formatKey(key)}
            </span>

            <span className="text-sm font-medium">
              {value}
            </span>
          </div>

          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-cyan-400"
              style={{
                width: `${(value / max) * 100}%`,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function ObservabilityPage() {
  const [ai, setAi] =
    useState<AIBreakdown | null>(null);

  const [agent, setAgent] =
    useState<AgentSummary | null>(null);

  const [kpis, setKpis] =
    useState<AgentKPIs | null>(null);

  const [roi, setRoi] =
    useState<AgentROI | null>(null);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState("");

  const [lastUpdated, setLastUpdated] =
    useState<Date | null>(null);

  const loadData = useCallback(async () => {
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

      setAi(aiData as AIBreakdown);
      setAgent(agentData as AgentSummary);
      setKpis(kpiData as AgentKPIs);
      setRoi(roiData as AgentROI);

      setLastUpdated(new Date());
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
    const timer = window.setTimeout(() => {
      void loadData();
    }, 0);
  
    return () => {
      window.clearTimeout(timer);
    };
  }, [loadData]);

  const sampleProgress = useMemo(() => {
    if (!roi) {
      return 0;
    }

    if (
      roi.minimum_autonomous_samples <= 0
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
    <div className="min-h-screen bg-[#070b14] text-white">
      <div className="flex min-h-screen">
        <aside className="hidden w-64 shrink-0 border-r border-slate-800 bg-[#090e18] p-5 lg:block">
          <div className="mb-8 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-cyan-400 text-slate-950">
              <BrainCircuit className="h-6 w-6" />
            </div>

            <div>
              <h1 className="font-semibold">
                CXOps AI
              </h1>

              <p className="text-xs text-slate-500">
                Control Center
              </p>
            </div>
          </div>

          <nav className="space-y-1">
            <SidebarItem
              href="/"
              icon={Gauge}
              label="Operations"
            />

            <SidebarItem
              href="/tickets"
              icon={Ticket}
              label="Tickets"
            />

            <SidebarItem
              href="/agent"
              icon={Bot}
              label="AI Agent"
            />

            <SidebarItem
              href="/approvals"
              icon={ShieldCheck}
              label="Approval Queue"
            />

            <SidebarItem
              href="/knowledge"
              icon={BrainCircuit}
              label="Knowledge / RAG"
            />

            <SidebarItem
              href="/runs"
              icon={Workflow}
              label="Agent Runs"
            />

            <SidebarItem
              href="/observability"
              icon={Activity}
              label="Observability"
              active
            />
          </nav>

          <div className="mt-10 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <div className="flex items-center gap-2 text-sm">
              <div className="h-2.5 w-2.5 rounded-full bg-emerald-400" />

              <span>
                Telemetry active
              </span>
            </div>

            <p className="mt-2 text-xs leading-5 text-slate-500">
              AI, agent, queue and ROI
              measurements
            </p>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="border-b border-slate-800 bg-[#090e18]/80 px-6 py-5 xl:px-10">
            <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
              <div>
                <p className="text-sm font-medium text-cyan-400">
                  Production Telemetry
                </p>

                <h2 className="mt-1 text-2xl font-semibold">
                  Observability
                </h2>

                <p className="mt-1 text-sm text-slate-500">
                  Measure AI quality, latency,
                  cost, agent reliability and
                  operational value.
                </p>
              </div>

              <div className="flex items-center gap-3">
                {lastUpdated && (
                  <span className="hidden text-xs text-slate-600 sm:block">
                    Updated{" "}
                    {lastUpdated.toLocaleTimeString()}
                  </span>
                )}

                <button
                  onClick={() =>
                    void loadData()
                  }
                  disabled={loading}
                  className="flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2 text-sm transition hover:bg-slate-800 disabled:opacity-50"
                >
                  <RefreshCw
                    className={`h-4 w-4 ${
                      loading
                        ? "animate-spin"
                        : ""
                    }`}
                  />

                  Refresh
                </button>
              </div>
            </div>
          </header>

          <div className="p-6 xl:p-10">
            {error && (
              <div className="mb-6 flex items-center gap-3 rounded-xl border border-red-900/50 bg-red-950/20 p-4 text-sm text-red-300">
                <XCircle className="h-5 w-5" />

                {error}
              </div>
            )}

            {loading && !ai ? (
              <div className="flex min-h-[65vh] items-center justify-center">
                <div className="text-center">
                  <RefreshCw className="mx-auto h-8 w-8 animate-spin text-cyan-400" />

                  <p className="mt-4 text-sm text-slate-500">
                    Loading observability telemetry...
                  </p>
                </div>
              </div>
            ) : (
              <>
                <section>
                  <div className="mb-4 flex items-center gap-2">
                    <BrainCircuit className="h-5 w-5 text-cyan-400" />

                    <h3 className="font-semibold">
                      AI Performance
                    </h3>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
                    <MetricCard
                      title="AI Requests"
                      value={`${ai?.overall.total_requests ?? 0}`}
                      subtitle="Persisted production AI requests"
                      icon={Bot}
                    />

                    <MetricCard
                      title="Success Rate"
                      value={`${ratioToPercent(
                        ai?.overall.success_rate ?? 0,
                      ).toFixed(2)}%`}
                      subtitle="Requests completed successfully"
                      icon={CheckCircle2}
                    />

                    <MetricCard
                      title="Grounded Rate"
                      value={`${ratioToPercent(
                        ai?.overall.grounded_rate ?? 0,
                      ).toFixed(2)}%`}
                      subtitle="Responses marked grounded"
                      icon={ShieldCheck}
                    />

                    <MetricCard
                      title="Avg Latency"
                      value={`${(
                        ai?.overall.avg_latency_ms ??
                        0
                      ).toFixed(0)} ms`}
                      subtitle="Average AI request latency"
                      icon={Clock3}
                    />

                    <MetricCard
                      title="Tokens"
                      value={(
                        ai?.overall.total_tokens ??
                        0
                      ).toLocaleString()}
                      subtitle="Total measured token usage"
                      icon={Zap}
                    />

                    <MetricCard
                      title="AI Cost"
                      value={`$${(
                        ai?.overall
                          .estimated_cost_usd ??
                        0
                      ).toFixed(6)}`}
                      subtitle="Estimated configured model cost"
                      icon={Coins}
                    />
                  </div>
                </section>

                <section className="mt-6 grid gap-6 xl:grid-cols-[1.2fr_1fr]">
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="mb-6">
                      <h3 className="font-semibold">
                        Agent Operational KPIs
                      </h3>

                      <p className="mt-1 text-sm text-slate-500">
                        Safety, autonomy and
                        execution behavior.
                      </p>
                    </div>

                    <div className="space-y-6">
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
                        label="Overall execution success"
                        value={
                          kpis?.execution_success_rate ??
                          0
                        }
                      />
                    </div>
                  </div>

                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="flex items-start justify-between">
                      <div>
                        <h3 className="font-semibold">
                          Durable Queue
                        </h3>

                        <p className="mt-1 text-sm text-slate-500">
                          Background integration
                          reliability.
                        </p>
                      </div>

                      <ServerCog className="h-5 w-5 text-cyan-400" />
                    </div>

                    <div className="mt-6 grid gap-3 sm:grid-cols-2">
                      <div className="rounded-xl bg-slate-950/60 p-4">
                        <p className="text-xs text-slate-500">
                          Total jobs
                        </p>

                        <p className="mt-2 text-2xl font-semibold">
                          {agent?.integration_jobs
                            .total ?? 0}
                        </p>
                      </div>

                      <div className="rounded-xl bg-slate-950/60 p-4">
                        <p className="text-xs text-slate-500">
                          Completed
                        </p>

                        <p className="mt-2 text-2xl font-semibold text-emerald-400">
                          {agent?.integration_jobs
                            .completed_jobs ?? 0}
                        </p>
                      </div>

                      <div className="rounded-xl bg-slate-950/60 p-4">
                        <p className="text-xs text-slate-500">
                          Retried jobs
                        </p>

                        <p className="mt-2 text-2xl font-semibold text-amber-300">
                          {agent?.integration_jobs
                            .retried_jobs ?? 0}
                        </p>
                      </div>

                      <div className="rounded-xl bg-slate-950/60 p-4">
                        <p className="text-xs text-slate-500">
                          Historical failed jobs
                        </p>

                        <p className="mt-2 text-2xl font-semibold text-red-300">
                          {agent?.integration_jobs
                            .failed_jobs ?? 0}
                        </p>
                      </div>
                    </div>

                    <div className="mt-6 space-y-4">
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

                    <div className="mt-6 rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                      <div className="flex justify-between text-sm">
                        <span className="text-slate-500">
                          Average attempts
                        </span>

                        <span className="font-semibold">
                          {kpis?.average_job_attempts ??
                            0}
                        </span>
                      </div>
                    </div>
                  </div>
                </section>

                <section className="mt-6 rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                  <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-start">
                    <div>
                      <div className="flex items-center gap-2">
                        <DollarSign className="h-5 w-5 text-emerald-400" />

                        <h3 className="font-semibold">
                          Automation Value & ROI
                        </h3>
                      </div>

                      <p className="mt-1 text-sm text-slate-500">
                        Estimated operational
                        value using configured
                        assumptions.
                      </p>
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

                  <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-6">
                    <div className="rounded-xl bg-slate-950/60 p-4">
                      <p className="text-xs text-slate-500">
                        Instrumented runs
                      </p>

                      <p className="mt-2 text-xl font-semibold">
                        {roi?.instrumented_runs ??
                          0}
                      </p>
                    </div>

                    <div className="rounded-xl bg-slate-950/60 p-4">
                      <p className="text-xs text-slate-500">
                        Autonomous samples
                      </p>

                      <p className="mt-2 text-xl font-semibold">
                        {roi?.instrumented_autonomous_executed_runs ??
                          0}
                      </p>
                    </div>

                    <div className="rounded-xl bg-slate-950/60 p-4">
                      <p className="text-xs text-slate-500">
                        Time saved
                      </p>

                      <p className="mt-2 text-xl font-semibold">
                        {roi?.estimated_minutes_saved ??
                          0}{" "}
                        min
                      </p>
                    </div>

                    <div className="rounded-xl bg-slate-950/60 p-4">
                      <p className="text-xs text-slate-500">
                        Labor savings
                      </p>

                      <p className="mt-2 text-xl font-semibold">
                        $
                        {(
                          roi?.estimated_labor_savings_usd ??
                          0
                        ).toFixed(2)}
                      </p>
                    </div>

                    <div className="rounded-xl bg-slate-950/60 p-4">
                      <p className="text-xs text-slate-500">
                        Agent AI cost
                      </p>

                      <p className="mt-2 text-xl font-semibold">
                        $
                        {(
                          roi?.agent_ai_cost_usd ??
                          0
                        ).toFixed(6)}
                      </p>
                    </div>

                    <div className="rounded-xl bg-slate-950/60 p-4">
                      <p className="text-xs text-slate-500">
                        Net savings
                      </p>

                      <p className="mt-2 text-xl font-semibold text-emerald-400">
                        $
                        {(
                          roi?.estimated_net_savings_usd ??
                          0
                        ).toFixed(2)}
                      </p>
                    </div>
                  </div>

                  <div className="mt-6 rounded-xl border border-amber-900/30 bg-amber-950/10 p-5">
                    <div className="flex gap-3">
                      <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />

                      <div className="w-full">
                        <div className="flex flex-col justify-between gap-2 sm:flex-row">
                          <div>
                            <p className="text-sm font-medium text-amber-300">
                              ROI sample-size guardrail
                            </p>

                            <p className="mt-1 text-xs leading-5 text-slate-500">
                              Formal ROI is withheld
                              until enough autonomous
                              execution samples have
                              been measured.
                            </p>
                          </div>

                          <span className="text-sm text-slate-400">
                            {roi?.instrumented_autonomous_executed_runs ??
                              0}
                            /
                            {roi?.minimum_autonomous_samples ??
                              0}
                          </span>
                        </div>

                        <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-800">
                          <div
                            className="h-full rounded-full bg-amber-400"
                            style={{
                              width: `${sampleProgress}%`,
                            }}
                          />
                        </div>

                        <p className="mt-3 text-xs text-slate-600">
                          Formal ROI:{" "}
                          {roi?.roi_percent !== null &&
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
                </section>

                <section className="mt-6">
                  <div className="mb-4 flex items-center gap-2">
                    <Activity className="h-5 w-5 text-cyan-400" />

                    <h3 className="font-semibold">
                      AI Feature Breakdown
                    </h3>
                  </div>

                  <div className="grid gap-6 xl:grid-cols-2">
                    {ai?.features.map(
                      (feature) => (
                        <div
                          key={feature.feature}
                          className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6"
                        >
                          <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
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

                            <div className="text-right">
                              <p className="text-xs text-slate-500">
                                Cost
                              </p>

                              <p className="mt-1 font-semibold">
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
                            <div className="rounded-xl bg-slate-950/60 p-4">
                              <p className="text-xs text-slate-500">
                                Latency
                              </p>

                              <p className="mt-2 font-semibold">
                                {feature.avg_latency_ms.toFixed(
                                  0,
                                )}{" "}
                                ms
                              </p>
                            </div>

                            <div className="rounded-xl bg-slate-950/60 p-4">
                              <p className="text-xs text-slate-500">
                                Input
                              </p>

                              <p className="mt-2 font-semibold">
                                {feature.input_tokens.toLocaleString()}
                              </p>
                            </div>

                            <div className="rounded-xl bg-slate-950/60 p-4">
                              <p className="text-xs text-slate-500">
                                Output
                              </p>

                              <p className="mt-2 font-semibold">
                                {feature.output_tokens.toLocaleString()}
                              </p>
                            </div>
                          </div>

                          <div className="mt-5 border-t border-slate-800 pt-5">
                            <p className="mb-3 text-xs uppercase tracking-wider text-slate-600">
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
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="flex items-center gap-2">
                      <Bot className="h-5 w-5 text-cyan-400" />

                      <h3 className="font-semibold">
                        Agent Actions
                      </h3>
                    </div>

                    <div className="mt-6">
                      <KeyValueList
                        data={
                          agent?.actions ?? {}
                        }
                      />
                    </div>
                  </div>

                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="flex items-center gap-2">
                      <Database className="h-5 w-5 text-cyan-400" />

                      <h3 className="font-semibold">
                        Agent Statuses
                      </h3>
                    </div>

                    <div className="mt-6">
                      <KeyValueList
                        data={
                          agent?.statuses ?? {}
                        }
                      />
                    </div>
                  </div>

                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="flex items-center gap-2">
                      <ShieldCheck className="h-5 w-5 text-cyan-400" />

                      <h3 className="font-semibold">
                        Tool Risk Levels
                      </h3>
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

                <section className="mt-6 rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                  <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
                    <div>
                      <div className="flex items-center gap-2">
                        <ServerCog className="h-5 w-5 text-cyan-400" />

                        <h3 className="font-semibold">
                          Production Measurement
                          Architecture
                        </h3>
                      </div>

                      <p className="mt-2 text-sm text-slate-500">
                        Persistent PostgreSQL
                        telemetry for historical
                        metrics plus Prometheus
                        counters for live API and
                        worker processes.
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
                        className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-950/50 px-4 py-3"
                      >
                        <CheckCircle2 className="h-4 w-4 text-emerald-400" />

                        <span className="text-sm text-slate-300">
                          {item}
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}