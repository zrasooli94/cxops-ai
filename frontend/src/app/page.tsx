"use client";

import {
  Activity,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  DollarSign,
  Gauge,
  Headphones,
  RefreshCw,
  ShieldCheck,
  Ticket,
  TriangleAlert,
  Users,
  Workflow,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

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

type Health = Record<string, unknown>;

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
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-slate-400">
            {title}
          </p>

          <p className="mt-3 text-3xl font-semibold tracking-tight text-white">
            {value}
          </p>
        </div>

        <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
          <Icon className="h-5 w-5 text-cyan-400" />
        </div>
      </div>

      <p className="mt-3 text-xs text-slate-500">
        {subtitle}
      </p>
    </div>
  );
}

function ProgressMetric({
  label,
  value,
}: {
  label: string;
  value: number;
}) {
  const safeValue = Math.min(Math.max(value, 0), 100);

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm text-slate-300">
          {label}
        </span>

        <span className="text-sm font-medium text-white">
          {value.toFixed(2)}%
        </span>
      </div>

      <div className="h-2 overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full rounded-full bg-cyan-400 transition-all"
          style={{ width: `${safeValue}%` }}
        />
      </div>
    </div>
  );
}

function NavItem({
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
      className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition ${
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

export default function Home() {
  const [kpis, setKpis] = useState<AgentKPIs | null>(null);
  const [roi, setRoi] = useState<AgentROI | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [lastUpdated, setLastUpdated] =
    useState<Date | null>(null);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [healthResponse, kpiResponse, roiResponse] =
        await Promise.all([
          fetch("/api/backend/health", {
            cache: "no-store",
          }),

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
        !healthResponse.ok ||
        !kpiResponse.ok ||
        !roiResponse.ok
      ) {
        throw new Error(
          "One or more CXOps services are unavailable.",
        );
      }

      const healthData =
        (await healthResponse.json()) as Health;

      const kpiData =
        (await kpiResponse.json()) as AgentKPIs;

      const roiData =
        (await roiResponse.json()) as AgentROI;

      setHealth(healthData);
      setKpis(kpiData);
      setRoi(roiData);

      setLastUpdated(new Date());
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to load dashboard.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadDashboard();
    }, 0);
  
    return () => {
      window.clearTimeout(timer);
    };
  }, [loadDashboard]);

  return (
    <div className="min-h-screen bg-[#070b14] text-white">
      <div className="flex min-h-screen">
        <aside className="hidden w-64 shrink-0 border-r border-slate-800 bg-[#090e18] p-5 lg:block">
          <div className="mb-8 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-cyan-400 text-slate-950">
              <BrainCircuit className="h-6 w-6" />
            </div>

            <div>
              <h1 className="font-semibold tracking-tight">
                CXOps AI
              </h1>

              <p className="text-xs text-slate-500">
                Control Center
              </p>
            </div>
          </div>

          <div className="space-y-1">
            <NavItem
              href="/"
              icon={Gauge}
              label="Operations"
              active
            />

            <NavItem
              href="/tickets"
              icon={Ticket}
              label="Tickets"
            />

            <NavItem
              href="/agent"
              icon={Bot}
              label="AI Agent"
            />

            <NavItem
              href="/approvals"
              icon={ShieldCheck}
              label="Approval Queue"
            />

            <NavItem
              href="/knowledge"
              icon={BrainCircuit}
              label="Knowledge / RAG"
            />

            <NavItem
              href="/runs"
              icon={Workflow}
              label="Agent Runs"
            />

            <NavItem
              href="/observability"
              icon={Activity}
              label="Observability"
            />
          </div>

          <div className="mt-10 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <div className="flex items-center gap-2 text-sm">
              <div
                className={`h-2.5 w-2.5 rounded-full ${
                  health
                    ? "bg-emerald-400"
                    : "bg-red-400"
                }`}
              />

              <span className="font-medium">
                Backend
              </span>
            </div>

            <p className="mt-2 text-xs text-slate-500">
              FastAPI + PostgreSQL + pgvector
            </p>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="border-b border-slate-800 bg-[#090e18]/80 px-6 py-5 backdrop-blur xl:px-10">
            <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
              <div>
                <p className="text-sm font-medium text-cyan-400">
                  CXOps AI
                </p>

                <h2 className="mt-1 text-2xl font-semibold tracking-tight">
                  Operations Dashboard
                </h2>

                <p className="mt-1 text-sm text-slate-500">
                  Live AI customer operations,
                  automation and reliability metrics.
                </p>
              </div>

              <div className="flex items-center gap-3">
                {lastUpdated && (
                  <span className="hidden text-xs text-slate-500 sm:block">
                    Updated{" "}
                    {lastUpdated.toLocaleTimeString()}
                  </span>
                )}

                <button
                  onClick={() =>
                    void loadDashboard()
                  }
                  disabled={loading}
                  className="flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2 text-sm font-medium transition hover:border-slate-600 hover:bg-slate-800 disabled:opacity-50"
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
              <div className="mb-6 flex items-center gap-3 rounded-xl border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">
                <XCircle className="h-5 w-5" />
                {error}
              </div>
            )}

            {loading && !kpis ? (
              <div className="flex min-h-[60vh] items-center justify-center">
                <div className="text-center">
                  <RefreshCw className="mx-auto h-7 w-7 animate-spin text-cyan-400" />

                  <p className="mt-3 text-sm text-slate-500">
                    Loading CXOps telemetry...
                  </p>
                </div>
              </div>
            ) : (
              <>
                <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <MetricCard
                    title="Total Agent Runs"
                    value={`${
                      kpis?.total_runs ?? 0
                    }`}
                    subtitle="Persisted production agent decisions"
                    icon={Bot}
                  />

                  <MetricCard
                    title="Autonomous Executions"
                    value={`${
                      kpis?.autonomous_executed_runs ??
                      0
                    }`}
                    subtitle={`${
                      kpis?.autonomous_execution_rate ??
                      0
                    }% of agent runs`}
                    icon={Workflow}
                  />

                  <MetricCard
                    title="Autonomous Success"
                    value={`${
                      kpis?.autonomous_success_rate ??
                      0
                    }%`}
                    subtitle="Successful low-risk autonomous actions"
                    icon={CheckCircle2}
                  />

                  <MetricCard
                    title="Execution Success"
                    value={`${
                      kpis?.execution_success_rate ??
                      0
                    }%`}
                    subtitle="Across executed agent workflows"
                    icon={Activity}
                  />
                </section>

                <section className="mt-6 grid gap-6 xl:grid-cols-[1.35fr_1fr]">
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="mb-6">
                      <h3 className="font-semibold">
                        Agent Decision Distribution
                      </h3>

                      <p className="mt-1 text-sm text-slate-500">
                        Current workflow routing and
                        human oversight rates.
                      </p>
                    </div>

                    <div className="space-y-6">
                      <ProgressMetric
                        label="Escalation rate"
                        value={
                          kpis?.escalation_rate ??
                          0
                        }
                      />

                      <ProgressMetric
                        label="Human review rate"
                        value={
                          kpis?.human_review_rate ??
                          0
                        }
                      />

                      <ProgressMetric
                        label="No-action rate"
                        value={
                          kpis?.no_action_rate ??
                          0
                        }
                      />

                      <ProgressMetric
                        label="Approval required"
                        value={
                          kpis?.approval_required_rate ??
                          0
                        }
                      />

                      <ProgressMetric
                        label="Pending approval"
                        value={
                          kpis?.pending_approval_rate ??
                          0
                        }
                      />
                    </div>
                  </div>

                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="flex items-start justify-between">
                      <div>
                        <h3 className="font-semibold">
                          Queue Reliability
                        </h3>

                        <p className="mt-1 text-sm text-slate-500">
                          Durable background job
                          processing.
                        </p>
                      </div>

                      <div className="rounded-xl bg-slate-800 p-3">
                        <Clock3 className="h-5 w-5 text-cyan-400" />
                      </div>
                    </div>

                    <div className="mt-7 space-y-4">
                      <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                        <div className="flex items-center gap-3">
                          <RefreshCw className="h-4 w-4 text-amber-400" />

                          <span className="text-sm text-slate-300">
                            Retry rate
                          </span>
                        </div>

                        <strong>
                          {kpis?.queue_retry_rate ??
                            0}
                          %
                        </strong>
                      </div>

                      <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                        <div className="flex items-center gap-3">
                          <TriangleAlert className="h-4 w-4 text-red-400" />

                          <span className="text-sm text-slate-300">
                            Failure rate
                          </span>
                        </div>

                        <strong>
                          {kpis?.queue_failure_rate ??
                            0}
                          %
                        </strong>
                      </div>

                      <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                        <div className="flex items-center gap-3">
                          <Activity className="h-4 w-4 text-emerald-400" />

                          <span className="text-sm text-slate-300">
                            Avg. job attempts
                          </span>
                        </div>

                        <strong>
                          {kpis?.average_job_attempts ??
                            0}
                        </strong>
                      </div>
                    </div>
                  </div>
                </section>

                <section className="mt-6 grid gap-6 xl:grid-cols-3">
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 xl:col-span-2">
                    <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                      <div>
                        <div className="flex items-center gap-2">
                          <DollarSign className="h-5 w-5 text-emerald-400" />

                          <h3 className="font-semibold">
                            Automation Value
                          </h3>
                        </div>

                        <p className="mt-2 text-sm text-slate-500">
                          Estimated operational
                          savings under configured
                          assumptions.
                        </p>
                      </div>

                      <span
                        className={`w-fit rounded-full px-3 py-1 text-xs font-medium ${
                          roi?.sample_size_sufficient
                            ? "bg-emerald-400/10 text-emerald-300"
                            : "bg-amber-400/10 text-amber-300"
                        }`}
                      >
                        {roi?.sample_size_sufficient
                          ? "Measurement ready"
                          : "Collecting samples"}
                      </span>
                    </div>

                    <div className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                      <div className="rounded-xl bg-slate-950/60 p-4">
                        <p className="text-xs text-slate-500">
                          Labor savings
                        </p>

                        <p className="mt-2 text-xl font-semibold">
                          $
                          {roi?.estimated_labor_savings_usd.toFixed(
                            2,
                          ) ?? "0.00"}
                        </p>
                      </div>

                      <div className="rounded-xl bg-slate-950/60 p-4">
                        <p className="text-xs text-slate-500">
                          AI cost
                        </p>

                        <p className="mt-2 text-xl font-semibold">
                          $
                          {roi?.agent_ai_cost_usd.toFixed(
                            6,
                          ) ??
                            "0.000000"}
                        </p>
                      </div>

                      <div className="rounded-xl bg-slate-950/60 p-4">
                        <p className="text-xs text-slate-500">
                          Net savings
                        </p>

                        <p className="mt-2 text-xl font-semibold text-emerald-400">
                          $
                          {roi?.estimated_net_savings_usd.toFixed(
                            2,
                          ) ?? "0.00"}
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
                    </div>

                    <div className="mt-5 rounded-xl border border-amber-900/30 bg-amber-950/20 p-4">
                      <div className="flex gap-3">
                        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />

                        <div>
                          <p className="text-sm font-medium text-amber-200">
                            ROI measurement
                            guardrail
                          </p>

                          <p className="mt-1 text-xs leading-5 text-slate-400">
                            Formal ROI is withheld
                            until at least{" "}
                            {
                              roi?.minimum_autonomous_samples
                            }{" "}
                            autonomous execution
                            samples are available.
                            Current instrumented
                            autonomous samples:{" "}
                            {
                              roi?.instrumented_autonomous_executed_runs
                            }
                            .
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <h3 className="font-semibold">
                      Automation Controls
                    </h3>

                    <p className="mt-1 text-sm text-slate-500">
                      Safety and human oversight.
                    </p>

                    <div className="mt-6 space-y-4">
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-slate-400">
                          Auto-approved runs
                        </span>

                        <span className="font-semibold">
                          {kpis?.auto_approved_runs ??
                            0}
                        </span>
                      </div>

                      <div className="flex items-center justify-between">
                        <span className="text-sm text-slate-400">
                          Approval required
                        </span>

                        <span className="font-semibold">
                          {kpis?.approval_required_rate ??
                            0}
                          %
                        </span>
                      </div>

                      <div className="flex items-center justify-between">
                        <span className="text-sm text-slate-400">
                          Human review
                        </span>

                        <span className="font-semibold">
                          {kpis?.human_review_rate ??
                            0}
                          %
                        </span>
                      </div>

                      <div className="mt-5 border-t border-slate-800 pt-5">
                        <div className="flex items-center gap-3 text-sm text-emerald-300">
                          <ShieldCheck className="h-5 w-5" />

                          Risk-based authorization
                          active
                        </div>
                      </div>
                    </div>
                  </div>
                </section>

                <section className="mt-6 rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                  <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                    <div>
                      <h3 className="font-semibold">
                        CXOps AI Platform
                      </h3>

                      <p className="mt-1 text-sm text-slate-500">
                        Production-style agentic
                        customer operations
                        architecture.
                      </p>
                    </div>

                    <div className="flex items-center gap-2 text-sm text-emerald-400">
                      <CheckCircle2 className="h-4 w-4" />

                      Systems operational
                    </div>
                  </div>

                  <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                    {[
                      ["FastAPI", Headphones],
                      ["LangGraph Agent", Bot],
                      [
                        "RAG + pgvector",
                        BrainCircuit,
                      ],
                      ["Zendesk", Users],
                      ["Durable Worker", Workflow],
                    ].map(([label, Icon]) => {
                      const Component =
                        Icon as typeof Activity;

                      return (
                        <div
                          key={label as string}
                          className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-950/50 px-4 py-3"
                        >
                          <Component className="h-4 w-4 text-cyan-400" />

                          <span className="text-sm text-slate-300">
                            {label as string}
                          </span>
                        </div>
                      );
                    })}
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