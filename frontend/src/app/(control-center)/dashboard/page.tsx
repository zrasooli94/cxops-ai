"use client";

import {
  Bot,
  CheckCircle2,
  Clock3,
  LoaderCircle,
  RefreshCw,
  TriangleAlert,
  Users,
} from "lucide-react";
import Link from "next/link";
import { type ComponentType, useCallback, useEffect, useState } from "react";

import DashboardNavCards from "./nav-cards";
import { CAPABILITIES } from "@/lib/authorization/capabilities";
import { useAuthorization } from "@/lib/authorization/context";
import {
  canViewServiceOperations,
  escalationSummaryToCounts,
  ESCALATION_SUMMARY_ENDPOINT,
} from "@/lib/dashboard/escalation-summary";

type OperationalMetrics = {
  unique_tickets_analyzed: number;
  pending_approvals: number;
  current_human_reviews: number;
  reviewed_runs: number;
  re_analysis_count: number;
  autonomous_executed_runs: number;
};

type ServiceOperationsSummary = {
  open: number;
  unassigned: number;
  needs_response: number;
  response_breaches: number;
  resolution_breaches: number;
  due_soon: number;
  urgent: number;
  high: number;
};

type EscalationCounts = {
  unacknowledged: number;
  breached: number;
};

function MetricCard({
  label,
  value,
  note,
  icon: Icon,
}: {
  label: string;
  value: number;
  note: string;
  icon: ComponentType<{ className?: string }>;
}) {
  return (
    <div className="app-panel rounded-[20px] p-6">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
        <Icon className="h-5 w-5" />
      </div>

      <p className="mt-5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
        {label}
      </p>

      <p className="editorial-number mt-2 text-4xl font-medium tracking-[-0.045em] text-slate-950">
        {value}
      </p>

      <p className="mt-3 text-xs leading-5 text-slate-500">
        {note}
      </p>
    </div>
  );
}

export default function DashboardPage() {
  const { can } = useAuthorization();
  const canReadMetrics = can(CAPABILITIES.OBSERVABILITY_READ);
  const canReadServiceOps = canViewServiceOperations(can);

  const [metrics, setMetrics] = useState<OperationalMetrics | null>(null);
  const [serviceOps, setServiceOps] = useState<ServiceOperationsSummary | null>(null);
  const [escalations, setEscalations] = useState<EscalationCounts | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadMetrics = useCallback(async () => {
    if (!canReadMetrics) {
      return;
    }

    setLoading(true);
    setError("");

    try {
      const response = await fetch("/api/backend/observability/agent/summary", {
        cache: "no-store",
      });

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? `Metrics API returned ${response.status}`);
      }

      const data = (await response.json()) as OperationalMetrics;
      setMetrics(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load metrics.");
    } finally {
      setLoading(false);
    }
  }, [canReadMetrics]);

  const loadServiceOps = useCallback(async () => {
    if (!canReadServiceOps) {
      return;
    }

    try {
      const response = await fetch("/api/backend/service-operations/summary", {
        cache: "no-store",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? `Service ops API returned ${response.status}`);
      }
      setServiceOps(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load service ops summary.");
    }
  }, [canReadServiceOps]);

  const loadEscalations = useCallback(async () => {
    if (!canReadServiceOps) {
      return;
    }

    try {
      const response = await fetch(ESCALATION_SUMMARY_ENDPOINT, {
        cache: "no-store",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? `Escalations API returned ${response.status}`);
      }
      setEscalations(escalationSummaryToCounts(await response.json()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load escalation counts.");
    }
  }, [canReadServiceOps]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadMetrics();
      void loadServiceOps();
      void loadEscalations();
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [loadMetrics, loadServiceOps, loadEscalations]);

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
                Control Center
              </p>
              <h1 className="mt-2 text-3xl font-light tracking-[-0.04em] text-slate-950">
                Dashboard
              </h1>
            </div>
          </div>
        </header>

        <div className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Operations Overview
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Control Center Dashboard
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Navigate to operational workspaces from here. All pages remain within the
              authenticated Control Center.
            </p>
          </section>

          <DashboardNavCards />

          {canReadServiceOps && (
            <section className="mb-12">
              <div className="mb-5 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-medium text-slate-950">
                    Service operations
                  </h2>
                  <p className="text-xs text-slate-400">
                    Queues, ownership & SLA snapshot
                  </p>
                </div>
                <Link
                  href="/operations"
                  className="text-sm font-medium text-violet-600 hover:underline"
                >
                  View operations →
                </Link>
              </div>

              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
                <MetricCard
                  label="Needs response"
                  value={serviceOps?.needs_response ?? 0}
                  note="Open tickets awaiting a public reply."
                  icon={TriangleAlert}
                />
                <MetricCard
                  label="Unassigned"
                  value={serviceOps?.unassigned ?? 0}
                  note="Open tickets with no owner."
                  icon={Users}
                />
                <MetricCard
                  label="SLA breached"
                  value={(serviceOps?.response_breaches ?? 0) + (serviceOps?.resolution_breaches ?? 0)}
                  note="Open tickets past first-response or resolution SLA."
                  icon={Clock3}
                />
                <MetricCard
                  label="Due soon"
                  value={serviceOps?.due_soon ?? 0}
                  note="Open tickets within 30 minutes of an SLA deadline."
                  icon={Clock3}
                />
                <Link
                  href="/operations?tab=escalations"
                  className="app-panel rounded-[20px] p-6 transition hover:border-violet-300 hover:shadow-md"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-50 text-rose-500">
                    <TriangleAlert className="h-5 w-5" />
                  </div>
                  <p className="mt-5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                    Escalations
                  </p>
                  <p className="editorial-number mt-2 text-4xl font-medium tracking-[-0.045em] text-slate-950">
                    {escalations?.unacknowledged ?? 0}
                  </p>
                  <p className="mt-3 text-xs leading-5 text-slate-500">
                    Unacknowledged escalations
                    {escalations && escalations.breached > 0
                      ? ` · ${escalations.breached} breached`
                      : ""}
                    .
                  </p>
                </Link>
              </div>
            </section>
          )}

          {canReadMetrics && (
            <section className="mb-12">
              <div className="mb-5 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-medium text-slate-950">
                    Operational metrics
                  </h2>

                  <p className="text-xs text-slate-400">
                    Live AI agent and approval telemetry
                  </p>
                </div>

                <button
                  type="button"
                  onClick={() => void loadMetrics()}
                  disabled={loading}
                  className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                  aria-label="Refresh metrics"
                >
                  <RefreshCw
                    className={`h-4 w-4 ${loading ? "animate-spin" : ""}`}
                  />
                </button>
              </div>

              {error && (
                <div className="mb-5 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
                  <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
                  {error}
                </div>
              )}

              {loading && !metrics ? (
                <div className="flex h-48 items-center justify-center rounded-[22px] border border-slate-200 bg-white">
                  <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
                </div>
              ) : (
                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                  <MetricCard
                    label="Tickets analyzed"
                    value={metrics?.unique_tickets_analyzed ?? 0}
                    note="Distinct tickets the agent has analyzed."
                    icon={Users}
                  />

                  <MetricCard
                    label="Pending approvals"
                    value={metrics?.pending_approvals ?? 0}
                    note="Runs awaiting reviewer approval."
                    icon={Clock3}
                  />

                  <MetricCard
                    label="Human reviews"
                    value={metrics?.current_human_reviews ?? 0}
                    note="Runs currently in human review."
                    icon={Bot}
                  />

                  <MetricCard
                    label="Autonomous executions"
                    value={metrics?.autonomous_executed_runs ?? 0}
                    note="Runs executed automatically without manual approval."
                    icon={CheckCircle2}
                  />
                </div>
              )}
            </section>
          )}

          <section className="rounded-2xl border border-slate-200 bg-white p-6 lg:p-8">
            <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-start">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-600">
                  Control Center
                </p>
                <h2 className="mt-2 text-2xl font-semibold tracking-[-0.025em] text-slate-950">
                  All operations in one authenticated workspace
                </h2>
                <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
                  The CXOps Control Center keeps support tickets, AI agent decisions,
                  RAG knowledge, human approvals, execution audit trails, and
                  operational observability in one authenticated, audit-ready workflow.
                </p>
              </div>

              <div className="flex items-center gap-2 rounded-full border border-violet-200 bg-violet-50/60 px-4 py-2 text-xs font-medium text-violet-600">
                <span className="h-2 w-2 rounded-full bg-emerald-400" />
                Authenticated session active
              </div>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
