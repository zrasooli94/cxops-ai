"use client";

import {
  Archive,
  FilePlus2,
  LoaderCircle,
  PlayCircle,
  Plus,
  RefreshCw,
  Scale,
  ShieldAlert,
  SlidersHorizontal,
  TriangleAlert,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

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
  archiveSimulation,
  assumptionSelectionError,
  assumptionValueError,
  buildScenarioCreatePayload,
  canManageSimulations,
  createSimulation,
  deriveSimulationExperience,
  EVALUATE_ACTION_LABEL,
  evaluateSimulation,
  fetchSimulationList,
  formatAssumptionSummary,
  formatTimestamp,
  formatWindowLabel,
  isFiniteAssumptionValue,
  REEVALUATE_ACTION_LABEL,
  scenarioNameError,
  SIMULATION_ASSUMPTION_DEFINITIONS,
  SIMULATION_ASSUMPTION_KEYS,
  SIMULATION_DEFAULT_DAYS,
  SIMULATION_DISCLAIMER,
  SIMULATION_STATUS_ARCHIVED,
  SIMULATION_WINDOW_DAYS,
  type ServiceTransformationScenario,
  type SimulationAssumptionKey,
  type SimulationAssumptionTargets,
} from "@/lib/simulation/simulation";

function AssumptionControl({
  definition,
  enabled,
  value,
  onEnabledChange,
  onValueChange,
}: {
  definition: (typeof SIMULATION_ASSUMPTION_DEFINITIONS)[number];
  enabled: boolean;
  value: number;
  onEnabledChange: (enabled: boolean) => void;
  onValueChange: (value: number) => void;
}) {
  const fieldId = `assumption-${definition.key}`;
  return (
    <div
      className={`rounded-2xl border p-4 transition ${
        enabled
          ? "border-violet-200 bg-violet-50/40"
          : "border-slate-200/70 bg-[#fbfcff]"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <label
          htmlFor={fieldId}
          className="flex cursor-pointer items-center gap-2.5 text-sm font-medium text-slate-800"
        >
          <input
            id={fieldId}
            type="checkbox"
            checked={enabled}
            onChange={(event) => onEnabledChange(event.target.checked)}
            className="h-4 w-4 rounded border-slate-300 accent-violet-600"
          />
          {definition.label}
        </label>

        <div className="flex items-center gap-2">
          <input
            aria-label={`${definition.label} target value`}
            type="number"
            min={0}
            max={100}
            step={0.1}
            value={enabled ? value : ""}
            disabled={!enabled}
            onChange={(event) => {
              const parsed = Number(event.target.value);
              onValueChange(Number.isFinite(parsed) ? parsed : 0);
            }}
            className="h-9 w-28 rounded-xl border border-slate-200 bg-white px-3 text-right text-sm text-slate-800 focus:border-violet-300 focus:outline-none disabled:opacity-40"
          />
          <span
            className={`text-sm ${enabled ? "text-slate-700" : "text-slate-400"}`}
          >
            %
          </span>
        </div>
      </div>

      {enabled && (
        <p className="mt-3 text-xs leading-5 text-slate-500">
          {definition.helper}
        </p>
      )}
    </div>
  );
}

function CreateScenarioForm({
  onCreated,
  onCancel,
}: {
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [days, setDays] = useState<number>(SIMULATION_DEFAULT_DAYS);
  const [enabled, setEnabled] = useState<
    Record<SimulationAssumptionKey, boolean>
  >({
    autonomous_execution_rate_target: false,
    human_approval_rate_target: false,
    knowledge_usage_rate_target: false,
    reopen_rate_target: false,
    sla_breach_reduction_percent: false,
  });
  const [values, setValues] = useState<
    Record<SimulationAssumptionKey, number>
  >({
    autonomous_execution_rate_target: 50,
    human_approval_rate_target: 50,
    knowledge_usage_rate_target: 50,
    reopen_rate_target: 10,
    sla_breach_reduction_percent: 25,
  });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const { refresh } = useAuthorization();

  const assumptions = useMemo<SimulationAssumptionTargets>(() => {
    const selected: SimulationAssumptionTargets = {};
    for (const key of SIMULATION_ASSUMPTION_KEYS) {
      if (enabled[key] && isFiniteAssumptionValue(values[key])) {
        selected[key] = values[key];
      }
    }
    return selected;
  }, [enabled, values]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();

    const nameError = scenarioNameError(name);
    if (nameError) {
      setError(nameError);
      return;
    }
    const selectionError = assumptionSelectionError(assumptions);
    if (selectionError) {
      setError(selectionError);
      return;
    }
    for (const key of SIMULATION_ASSUMPTION_KEYS) {
      const valueError = assumptionValueError(values[key]);
      if (valueError) {
        setError(valueError);
        return;
      }
    }

    setCreating(true);
    setError("");
    try {
      await createSimulation(
        buildScenarioCreatePayload({
          name,
          description: description.length > 0 ? description : null,
          days,
          assumptions,
        }),
      );
      setName("");
      setDescription("");
      setDays(SIMULATION_DEFAULT_DAYS);
      setEnabled({
        autonomous_execution_rate_target: false,
        human_approval_rate_target: false,
        knowledge_usage_rate_target: false,
        reopen_rate_target: false,
        sla_breach_reduction_percent: false,
      });
      onCreated();
    } catch (err) {
      if (
        err instanceof Object &&
        "status" in err &&
        "detail" in err
      ) {
        const plan = planClientAuthorizationResponse(
          (err as { status: number }).status,
          (err as { detail: string | null }).detail,
        );
        const feedback = authorizationFeedback(plan);
        if (feedback) {
          setError(feedback);
          refresh();
          return;
        }
      }
      setError(
        err instanceof Error
          ? err.message
          : "Could not create the scenario.",
      );
    } finally {
      setCreating(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="app-panel rounded-[22px] p-6 md:p-7"
      aria-label="Create transformation scenario"
    >
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium text-slate-900">Create scenario</h2>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            Define the observed window and the 0–100 percentage assumptions to
            model, then evaluate to capture the observed baseline and project
            impact.
          </p>
        </div>

        <button
          type="button"
          onClick={onCancel}
          className="flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:text-slate-800"
          aria-label="Close create scenario form"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="mb-6 grid gap-4 md:grid-cols-2">
        <div>
          <label
            htmlFor="scenario-name"
            className="mb-1.5 block text-xs font-medium text-slate-700"
          >
            Scenario name
          </label>
          <input
            id="scenario-name"
            type="text"
            maxLength={255}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Increase low-risk autonomy"
            className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          />
        </div>

        <div>
          <label
            htmlFor="scenario-window"
            className="mb-1.5 block text-xs font-medium text-slate-700"
          >
            Observed window
          </label>
          <select
            id="scenario-window"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
            className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          >
            {SIMULATION_WINDOW_DAYS.map((option) => (
              <option key={option} value={option}>
                {formatWindowLabel(option)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label
          htmlFor="scenario-description"
          className="mb-1.5 block text-xs font-medium text-slate-700"
        >
          Description{" "}
          <span className="font-normal text-slate-400">(optional)</span>
        </label>
        <textarea
          id="scenario-description"
          maxLength={2000}
          rows={2}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
        />
      </div>

      <div className="mt-6 mb-3 flex items-center gap-2">
        <SlidersHorizontal className="h-4 w-4 text-violet-500" />
        <h3 className="text-sm font-medium text-slate-900">
          Assumptions{" "}
          <span className="font-normal text-slate-400">
            — enable at least one
          </span>
        </h3>
      </div>

      <div className="space-y-3">
        {SIMULATION_ASSUMPTION_DEFINITIONS.map((definition) => (
          <AssumptionControl
            key={definition.key}
            definition={definition}
            enabled={enabled[definition.key]}
            value={values[definition.key]}
            onEnabledChange={(checked) =>
              setEnabled((current) => ({
                ...current,
                [definition.key]: checked,
              }))
            }
            onValueChange={(value) =>
              setValues((current) => ({
                ...current,
                [definition.key]: value,
              }))
            }
          />
        ))}
      </div>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={creating}
          className="flex h-10 items-center gap-2 rounded-xl bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
        >
          {creating ? (
            <LoaderCircle className="h-4 w-4 animate-spin" />
          ) : (
            <FilePlus2 className="h-4 w-4" />
          )}
          {creating ? "Creating…" : "Create scenario"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="h-10 rounded-xl border border-slate-200 bg-white px-4 text-sm text-slate-600 transition hover:text-slate-900"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

interface ScenarioActionsProps {
  scenario: ServiceTransformationScenario;
  canManage: boolean;
  selected: boolean;
  onToggleCompare: (id: number) => void;
  onEvaluate: (id: number) => void;
  onArchive: (id: number) => void;
  busyId: number | null;
}

function ScenarioActions({
  scenario,
  canManage,
  selected,
  onToggleCompare,
  onEvaluate,
  onArchive,
  busyId,
}: ScenarioActionsProps) {
  const archived = scenario.status === SIMULATION_STATUS_ARCHIVED;
  const busy = busyId === scenario.id;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={() => onToggleCompare(scenario.id)}
        aria-pressed={selected}
        className={`flex h-9 items-center gap-2 rounded-xl border px-3 text-xs font-medium transition ${
          selected
            ? "border-violet-300 bg-violet-50 text-violet-700"
            : "border-slate-200 bg-white text-slate-600 hover:text-slate-900"
        }`}
      >
        <Scale className="h-3.5 w-3.5" />
        {selected ? "Selected" : "Compare"}
      </button>

      {canManage && !archived && (
        <>
          <button
            type="button"
            onClick={() => onEvaluate(scenario.id)}
            disabled={busy}
            className="flex h-9 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:border-violet-300 hover:text-violet-700 disabled:opacity-50"
          >
            {busy ? (
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <PlayCircle className="h-3.5 w-3.5" />
            )}
            {scenario.status === "evaluated"
              ? REEVALUATE_ACTION_LABEL
              : EVALUATE_ACTION_LABEL}
          </button>

          <button
            type="button"
            onClick={() => onArchive(scenario.id)}
            disabled={busy}
            className="flex h-9 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:border-rose-300 hover:text-rose-700 disabled:opacity-50"
          >
            <Archive className="h-3.5 w-3.5" />
            Archive
          </button>
        </>
      )}
    </div>
  );
}

function ScenarioCard({
  scenario,
  canManage,
  selected,
  onToggleCompare,
  onEvaluate,
  onArchive,
  busyId,
}: {
  scenario: ServiceTransformationScenario;
  canManage: boolean;
  selected: boolean;
  onToggleCompare: (id: number) => void;
  onEvaluate: (id: number) => void;
  onArchive: (id: number) => void;
  busyId: number | null;
}) {
  const archived = scenario.status === SIMULATION_STATUS_ARCHIVED;

  return (
    <div
      className={`rounded-[20px] border p-5 transition ${
        archived
          ? "border-slate-200 bg-slate-50/60 opacity-75"
          : "border-slate-200/80 bg-white/80"
      } ${selected ? "ring-2 ring-violet-300" : ""}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <Link
            href={`/simulations/${scenario.id}`}
            className="text-[15px] font-medium text-slate-900 transition hover:text-violet-700"
          >
            {scenario.name}
          </Link>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <SimulationStatusBadge status={scenario.status} />
            <Badge>{formatWindowLabel(scenario.window_days)}</Badge>
            {scenario.formula_version && (
              <Badge variant="info">v{scenario.formula_version}</Badge>
            )}
          </div>
        </div>

        <ScenarioActions
          scenario={scenario}
          canManage={canManage}
          selected={selected}
          onToggleCompare={onToggleCompare}
          onEvaluate={onEvaluate}
          onArchive={onArchive}
          busyId={busyId}
        />
      </div>

      {scenario.description && (
        <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">
          {scenario.description}
        </p>
      )}

      <div className="mt-4 grid gap-2 text-xs text-slate-500 sm:grid-cols-2">
        <div>
          <span className="font-medium text-slate-400">Assumptions:</span>{" "}
          {formatAssumptionSummary(scenario.assumptions) || "—"}
        </div>
        <div>
          <span className="font-medium text-slate-400">Last evaluated:</span>{" "}
          {formatTimestamp(scenario.evaluated_at)}
        </div>
        {scenario.created_by_subject && (
          <div className="sm:col-span-2">
            <span className="font-medium text-slate-400">Created by:</span>{" "}
            {scenario.created_by_subject}
          </div>
        )}
      </div>
    </div>
  );
}

type LibraryFilter = "active" | "archived";

function ScenarioLibraryBody({
  loadError,
  loading,
  scenarios,
  visibleScenarios,
  filter,
  canManage,
  setCreateOpen,
  onRetry,
  onToggleCompare,
  onEvaluate,
  onArchive,
  busyId,
  selected,
}: {
  loadError: string;
  loading: boolean;
  scenarios: ServiceTransformationScenario[];
  visibleScenarios: ServiceTransformationScenario[];
  filter: LibraryFilter;
  canManage: boolean;
  setCreateOpen: (open: boolean) => void;
  onRetry: () => void;
  onToggleCompare: (id: number) => void;
  onEvaluate: (id: number) => void;
  onArchive: (id: number) => void;
  busyId: number | null;
  selected: Set<number>;
}) {
  if (loadError) {
    return (
      <div className="my-10 flex flex-col items-center gap-3 rounded-[20px] border border-rose-200 bg-rose-50/60 p-10 text-center">
        <TriangleAlert className="h-6 w-6 text-rose-500" />
        <p className="text-sm text-rose-700">{loadError}</p>
        <button
          type="button"
          onClick={onRetry}
          className="rounded-xl border border-rose-200 bg-white px-4 py-2 text-xs font-medium text-rose-700 transition hover:bg-rose-50"
        >
          Try again
        </button>
      </div>
    );
  }

  if (loading && scenarios.length === 0) {
    return (
      <div className="my-10 flex items-center justify-center gap-3 rounded-[20px] border border-slate-200 bg-white/70 p-10">
        <LoaderCircle className="h-5 w-5 animate-spin text-violet-500" />
        <p className="text-sm text-slate-500">
          Loading transformation scenarios…
        </p>
      </div>
    );
  }

  if (visibleScenarios.length === 0) {
    return (
      <div className="my-10 rounded-[20px] border border-slate-200 bg-white/70 p-10 text-center">
        <SlidersHorizontal className="mx-auto h-6 w-6 text-slate-300" />
        <p className="mt-3 text-sm font-medium text-slate-700">
          {filter === "archived"
            ? "No archived scenarios."
            : "No transformation scenarios yet."}
        </p>
        <p className="mx-auto mt-1 max-w-md text-xs leading-5 text-slate-500">
          Create a scenario with deterministic assumptions, then evaluate it to
          capture the current observed baseline and calculate projections.
        </p>
        {canManage && (
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="mt-4 flex h-10 items-center gap-2 rounded-xl bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700"
          >
            <Plus className="h-4 w-4" />
            Create scenario
          </button>
        )}
      </div>
    );
  }

  return (
    <section className="grid gap-4 xl:grid-cols-2">
      {visibleScenarios.map((scenario) => (
        <ScenarioCard
          key={scenario.id}
          scenario={scenario}
          canManage={canManage}
          selected={selected.has(scenario.id)}
          onToggleCompare={onToggleCompare}
          onEvaluate={onEvaluate}
          onArchive={onArchive}
          busyId={busyId}
        />
      ))}
    </section>
  );
}

export default function SimulationsPage() {
  const router = useRouter();
  const { can, refresh } = useAuthorization();
  const experience = deriveSimulationExperience(can);
  const canManage = canManageSimulations(can);

  const [scenarios, setScenarios] = useState<ServiceTransformationScenario[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [filter, setFilter] = useState<LibraryFilter>("active");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState("");

  const loadScenarios = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      setScenarios(await fetchSimulationList());
    } catch (err) {
      if (
        err instanceof Object &&
        "status" in err &&
        "detail" in err
      ) {
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
        err instanceof Error ? err.message : "Could not load transformation scenarios.",
      );
    } finally {
      setLoading(false);
    }
  }, [refresh]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadScenarios();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadScenarios]);

  const visibleScenarios = useMemo(() => {
    const sorted = [...scenarios].sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
    return filter === "archived"
      ? sorted.filter((scenario) => scenario.status === SIMULATION_STATUS_ARCHIVED)
      : sorted.filter((scenario) => scenario.status !== SIMULATION_STATUS_ARCHIVED);
  }, [scenarios, filter]);

  const activeCount = useMemo(
    () => scenarios.filter((s) => s.status !== SIMULATION_STATUS_ARCHIVED).length,
    [scenarios],
  );
  const archivedCount = useMemo(
    () => scenarios.filter((s) => s.status === SIMULATION_STATUS_ARCHIVED).length,
    [scenarios],
  );

  function toggleCompare(id: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < 4) {
        next.add(id);
      }
      return next;
    });
  }

  function goToCompare() {
    if (selected.size < 2) {
      setActionError("Select at least two scenarios to compare.");
      return;
    }
    router.push(`/simulations/compare?ids=${[...selected].sort((a, b) => a - b).join(",")}`);
  }

  const handleEvaluate = useCallback(
    async (scenarioId: number) => {
      setBusyId(scenarioId);
      setActionError("");
      try {
        await evaluateSimulation(scenarioId);
        await loadScenarios();
        router.push(`/simulations/${scenarioId}`);
      } catch (err) {
        if (
          err instanceof Object &&
          "status" in err &&
          "detail" in err
        ) {
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
        setBusyId(null);
      }
    },
    [loadScenarios, refresh, router],
  );

  const handleArchive = useCallback(
    async (scenarioId: number) => {
      setBusyId(scenarioId);
      setActionError("");
      try {
        await archiveSimulation(scenarioId);
        setSelected((current) => {
          const next = new Set(current);
          next.delete(scenarioId);
          return next;
        });
        await loadScenarios();
      } catch (err) {
        if (
          err instanceof Object &&
          "status" in err &&
          "detail" in err
        ) {
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
        setBusyId(null);
      }
    },
    [loadScenarios, refresh],
  );

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="no-print fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between gap-4 px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Service Transformation Simulation
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Model operational “what-if” scenarios using observed service data
                and deterministic assumptions.
              </p>
            </div>

            <div className="flex items-center gap-3">
              {selected.size >= 2 && (
                <button
                  type="button"
                  onClick={goToCompare}
                  className="flex h-10 items-center gap-2 rounded-xl border border-violet-300 bg-violet-50 px-4 text-sm font-medium text-violet-700 transition hover:bg-violet-100"
                >
                  <Scale className="h-4 w-4" />
                  Compare ({selected.size})
                </button>
              )}

              {canManage && (
                <button
                  type="button"
                  onClick={() => setCreateOpen((open) => !open)}
                  className="flex h-10 items-center gap-2 rounded-xl bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700"
                >
                  {createOpen ? (
                    <X className="h-4 w-4" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                  {createOpen ? "Close" : "Create scenario"}
                </button>
              )}
            </div>
          </div>
        </header>

        <main className="print-area mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Executive decision workspace
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Service transformation,
              <span className="gradient-text"> explored before it happens.</span>
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Create deterministic what-if scenarios over observed service data,
              evaluate them against the authoritative simulation engine, and
              compare outcomes side-by-side — without touching live operations.
            </p>
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
                    You can browse, open, and compare evaluated scenarios in this
                    workspace. Creating, evaluating, and archiving scenarios
                    require the manage capability.
                  </p>
                </div>
              </div>
            </div>
          )}

          <section className="mb-8 rounded-[20px] border border-amber-200 bg-amber-50/55 p-5">
            <div className="flex gap-3">
              <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
              <div>
                <p className="text-sm font-medium text-amber-800">
                  Deterministic estimates only
                </p>
                <p className="mt-1 max-w-3xl text-xs leading-5 text-amber-700/80">
                  {SIMULATION_DISCLAIMER} Scenario models never mutate live
                  tickets, runs, SLA policies, or authorization rules.
                </p>
              </div>
            </div>
          </section>

          {createOpen && canManage && (
            <section className="mb-8">
              <CreateScenarioForm
                onCreated={() => {
                  setCreateOpen(false);
                  void loadScenarios();
                }}
                onCancel={() => setCreateOpen(false)}
              />
            </section>
          )}

          <section className="mb-6 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-slate-900">
                Scenario library
              </h2>
              <Badge variant="violet">
                {filter === "active" ? activeCount : archivedCount}
              </Badge>
            </div>

            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 rounded-full border border-slate-200 bg-white/80 p-1 text-[11px] text-slate-500 shadow-sm">
                {(
                  [
                    { id: "active", label: "Active" },
                    { id: "archived", label: "Archived" },
                  ] as const
                ).map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    onClick={() => setFilter(option.id)}
                    className={`rounded-full px-3 py-1.5 font-medium transition ${
                      filter === option.id
                        ? "bg-violet-500 text-white shadow-sm"
                        : "text-slate-500 hover:text-slate-800"
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>

              <button
                type="button"
                onClick={() => void loadScenarios()}
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh scenarios"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </button>
            </div>
          </section>

          <ScenarioLibraryBody
            loadError={loadError}
            loading={loading}
            scenarios={scenarios}
            visibleScenarios={visibleScenarios}
            filter={filter}
            canManage={canManage}
            setCreateOpen={setCreateOpen}
            onRetry={() => void loadScenarios()}
            onToggleCompare={toggleCompare}
            onEvaluate={(id) => void handleEvaluate(id)}
            onArchive={(id) => void handleArchive(id)}
            busyId={busyId}
            selected={selected}
          />

          {actionError && (
            <div className="mt-6 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
              {actionError}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}