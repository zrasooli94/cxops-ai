"use client";

import {
  ArrowLeft,
  LoaderCircle,
  Scale,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import {
  COMPARISON_MAX_SCENARIOS,
  COMPARISON_MIN_SCENARIOS,
  comparisonSelectionError,
  comparisonWarningsFor,
  deltaToneClass,
  fetchSimulation,
  formatAssumptionSummary,
  formatCount,
  formatDelta,
  formatMoney,
  formatMoneyDelta,
  formatRate,
  formatTimestamp,
  formatWindowLabel,
  presentationDelta,
  SIMULATION_DISCLAIMER,
  type ServiceTransformationScenario,
} from "@/lib/simulation/simulation";

type RowKind = "count" | "rate" | "money";

interface MatrixRow {
  label: string;
  kind: RowKind;
  observed: (scenario: ServiceTransformationScenario) => number | null;
  projected: (scenario: ServiceTransformationScenario) => number | null;
}

function formatSignedRate(delta: number): string {
  if (delta > 0) return `+${delta}%`;
  if (delta < 0) return `${delta}%`;
  return "0%";
}

function deltaCell(
  row: MatrixRow,
  observed: number | null,
  projected: number | null,
): string {
  const delta = presentationDelta(projected, observed);
  if (delta === null) return "—";
  if (row.kind === "money") return formatMoneyDelta(delta);
  if (row.kind === "rate") return formatSignedRate(delta);
  return formatDelta(delta);
}

function valueCell(row: MatrixRow, value: number | null): string {
  if (value === null || value === undefined) return "—";
  if (row.kind === "money") return formatMoney(value);
  if (row.kind === "rate") return formatRate(value);
  return formatCount(value);
}

const MATRIX_ROWS: readonly MatrixRow[] = [
  {
    label: "Autonomous executions",
    kind: "count",
    observed: (s) => s.observed_baseline?.autonomous_executions ?? null,
    projected: (s) => s.projected_result?.autonomous_executions ?? null,
  },
  {
    label: "Autonomous execution rate",
    kind: "rate",
    observed: (s) => s.observed_baseline?.rates.autonomous_execution_rate ?? null,
    projected: (s) => s.projected_result?.autonomous_execution_rate_percent ?? null,
  },
  {
    label: "Human approval demand",
    kind: "count",
    observed: (s) => s.observed_baseline?.human_approval_required ?? null,
    projected: (s) => s.projected_result?.human_approval_required ?? null,
  },
  {
    label: "Human approval rate",
    kind: "rate",
    observed: (s) => s.observed_baseline?.rates.human_approval_rate ?? null,
    projected: (s) => s.projected_result?.human_approval_rate_percent ?? null,
  },
  {
    label: "Knowledge specialist runs",
    kind: "count",
    observed: (s) => s.observed_baseline?.knowledge_specialist_runs ?? null,
    projected: (s) => s.projected_result?.knowledge_specialist_runs ?? null,
  },
  {
    label: "Knowledge usage rate",
    kind: "rate",
    observed: (s) => s.observed_baseline?.rates.knowledge_usage_rate ?? null,
    projected: (s) => s.projected_result?.knowledge_usage_rate_percent ?? null,
  },
  {
    label: "Reopened tickets",
    kind: "count",
    observed: (s) => s.observed_baseline?.reopened_tickets ?? null,
    projected: (s) => s.projected_result?.reopened_tickets ?? null,
  },
  {
    label: "Reopen rate",
    kind: "rate",
    observed: (s) => s.observed_baseline?.rates.reopen_rate ?? null,
    projected: (s) => s.projected_result?.reopen_rate_percent ?? null,
  },
  {
    label: "SLA breaches",
    kind: "count",
    observed: (s) => s.observed_baseline?.total_sla_breaches ?? null,
    projected: (s) => s.projected_result?.total_sla_breaches ?? null,
  },
];

function MatrixTable({
  scenarios,
}: {
  scenarios: readonly ServiceTransformationScenario[];
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left">
            <th className="pb-2 pr-4 text-[11px] font-medium uppercase tracking-wide text-slate-400">
              Metric
            </th>
            {scenarios.map((scenario) => (
              <th key={scenario.id} className="pb-2 pr-4 text-right">
                <Link
                  href={`/simulations/${scenario.id}`}
                  className="text-xs font-semibold text-slate-800 transition hover:text-violet-700"
                >
                  {scenario.name}
                </Link>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {MATRIX_ROWS.map((row) => (
            <tr key={row.label} className="border-b border-slate-100">
              <td className="py-2.5 pr-4 text-xs font-medium text-slate-700">
                {row.label}
              </td>
              {scenarios.map((scenario) => {
                const observed = row.observed(scenario);
                const projected = row.projected(scenario);
                const delta = presentationDelta(projected, observed);
                return (
                  <td key={scenario.id} className="py-2.5 pr-4 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <span className="tabular-nums text-slate-400">
                        {valueCell(row, observed)}
                      </span>
                      <span className="arrow">→</span>
                      <span className="tabular-nums font-medium text-slate-900">
                        {valueCell(row, projected)}
                      </span>
                      <span
                        className={`w-14 text-right text-xs tabular-nums ${deltaToneClass(delta)}`}
                      >
                        {deltaCell(row, observed, projected)}
                      </span>
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}

          <tr className="border-b border-slate-100">
            <td className="py-2.5 pr-4 text-xs font-medium text-slate-700">
              Estimated labor savings (projected)
            </td>
            {scenarios.map((scenario) => (
              <td key={scenario.id} className="py-2.5 pr-4 text-right tabular-nums text-slate-900">
                {formatMoney(scenario.projected_result?.value.estimated_labor_savings_usd)}
              </td>
            ))}
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-2.5 pr-4 text-xs font-medium text-slate-700">
              Projected AI cost
            </td>
            {scenarios.map((scenario) => (
              <td key={scenario.id} className="py-2.5 pr-4 text-right tabular-nums text-slate-900">
                {formatMoney(scenario.projected_result?.value.agent_ai_cost_usd)}
              </td>
            ))}
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-2.5 pr-4 text-xs font-medium text-slate-700">
              Estimated net savings (projected)
            </td>
            {scenarios.map((scenario) => (
              <td key={scenario.id} className="py-2.5 pr-4 text-right tabular-nums text-slate-900">
                {formatMoney(scenario.projected_result?.value.estimated_net_savings_usd)}
              </td>
            ))}
          </tr>
          <tr>
            <td className="py-2.5 pr-4 text-xs font-medium text-slate-700">
              Projected ROI
            </td>
            {scenarios.map((scenario) => (
              <td key={scenario.id} className="py-2.5 pr-4 text-right tabular-nums">
                {scenario.projected_result?.value.roi_percent === null ||
                scenario.projected_result?.value.roi_percent === undefined ? (
                  <span className="text-slate-400">—</span>
                ) : (
                  <span className="font-semibold text-violet-700">
                    {formatRate(scenario.projected_result.value.roi_percent)}
                  </span>
                )}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      <p className="mt-3 text-[11px] leading-5 text-slate-400">
        Deltas shown are presentation differences between the projected and
        observed values returned by the simulation engine. This comparison
        never ranks or recommends scenarios.
      </p>
    </div>
  );
}

function FocusScenarioCard({
  scenario,
}: {
  scenario: ServiceTransformationScenario;
}) {
  return (
    <div className="app-panel rounded-[22px] p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link
            href={`/simulations/${scenario.id}`}
            className="text-sm font-medium text-slate-900 transition hover:text-violet-700"
          >
            {scenario.name}
          </Link>
          <p className="mt-1 text-[11px] text-slate-400">
            {formatWindowLabel(scenario.window_days)} ·{" "}
            {formatTimestamp(scenario.evaluated_at)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] text-slate-600">
            {scenario.status}
          </span>
          {scenario.formula_version && (
            <span className="inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-[11px] font-medium text-blue-700">
              v{scenario.formula_version}
            </span>
          )}
        </div>
      </div>

      <p className="mt-3 text-[11px] leading-5 text-slate-500">
        {formatAssumptionSummary(scenario.assumptions) || "No assumptions set."}
      </p>
    </div>
  );
}

function CompareContent() {
  const searchParams = useSearchParams();

  const ids = useMemo(() => {
    const raw = searchParams.get("ids") ?? "";
    const parsed = raw
      .split(",")
      .map((part) => Number(part.trim()))
      .filter((value) => Number.isInteger(value) && value > 0);
    return [...new Set(parsed)].sort((a, b) => a - b);
  }, [searchParams]);

  const selectionError = useMemo(
    () => comparisonSelectionError(ids.length),
    [ids],
  );

  const [scenarios, setScenarios] = useState<ServiceTransformationScenario[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");

  const load = useCallback(async () => {
    if (selectionError) return;
    setLoading(true);
    setLoadError("");
    try {
      const results = await Promise.all(ids.map((id) => fetchSimulation(id)));
      setScenarios(results);
    } catch (err) {
      setLoadError(
        err instanceof Error
          ? err.message
          : "Could not load the selected scenarios.",
      );
    } finally {
      setLoading(false);
    }
  }, [ids, selectionError]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  if (selectionError) {
    return (
      <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
        <div className="my-10 flex flex-col items-center gap-3 rounded-[20px] border border-slate-200 bg-white/70 p-10 text-center">
          <Scale className="h-6 w-6 text-slate-300" />
          <p className="text-sm text-slate-600">{selectionError}</p>
          <span className="text-xs text-slate-400">
            Compare {COMPARISON_MIN_SCENARIOS}–{COMPARISON_MAX_SCENARIOS} scenarios.
          </span>
          <Link
            href="/simulations"
            className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-700 transition hover:text-violet-700"
          >
            Back to scenario library
          </Link>
        </div>
      </main>
    );
  }

  const warnings = comparisonWarningsFor(scenarios);

  return (
    <main className="print-area mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
      <section className="mb-6">
        <Link
          href="/simulations"
          className="inline-flex items-center gap-1.5 text-xs text-slate-500 transition hover:text-violet-700"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to scenario library
        </Link>

        <h1 className="mt-4 text-3xl font-light tracking-[-0.045em] text-slate-950 md:text-4xl">
          Scenario comparison
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          Deterministic side-by-side preview of evaluated scenarios. This
          comparison never ranks or recommends a scenario — the decision stays
          with the operator.
        </p>
      </section>

      <section className="mb-8 rounded-[20px] border border-amber-200 bg-amber-50/55 p-5">
        <div className="flex gap-3">
          <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
          <div>
            <p className="text-sm font-medium text-amber-800">
              Deterministic estimates only
            </p>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-amber-700/80">
              {SIMULATION_DISCLAIMER} Deltas are presentation differences
              between projected and observed values returned by the engine.
            </p>
          </div>
        </div>
      </section>

      {warnings.length > 0 && (
        <section className="mb-8 rounded-[20px] border border-orange-200 bg-orange-50/60 p-5">
          <div className="flex gap-3">
            <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-orange-500" />
            <ul className="space-y-1.5 text-xs leading-5 text-orange-800">
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        </section>
      )}

      {loading && scenarios.length === 0 ? (
        <div className="my-10 flex items-center justify-center gap-3 rounded-[20px] border border-slate-200 bg-white/70 p-10">
          <LoaderCircle className="h-5 w-5 animate-spin text-violet-500" />
          <p className="text-sm text-slate-500">Loading scenarios…</p>
        </div>
      ) : loadError ? (
        <div className="my-10 flex flex-col items-center gap-3 rounded-[20px] border border-rose-200 bg-rose-50/60 p-10 text-center">
          <TriangleAlert className="h-6 w-6 text-rose-500" />
          <p className="text-sm text-rose-700">{loadError}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-xl border border-rose-200 bg-white px-4 py-2 text-xs font-medium text-rose-700 transition hover:bg-rose-50"
          >
            Try again
          </button>
        </div>
      ) : (
        <>
          <section className="mb-6 grid gap-4 lg:grid-cols-2">
            {scenarios.map((scenario) => (
              <FocusScenarioCard key={scenario.id} scenario={scenario} />
            ))}
          </section>

          <section>
            <MatrixTable scenarios={scenarios} />
          </section>
        </>
      )}
    </main>
  );
}

export default function ComparePage() {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center">
          <LoaderCircle className="h-8 w-8 animate-spin text-violet-500" />
        </main>
      }
    >
      <CompareContent />
    </Suspense>
  );
}