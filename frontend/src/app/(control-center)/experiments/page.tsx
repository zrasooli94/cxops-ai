"use client";

import {
  ClipboardCheck,
  FlaskConical,
  LoaderCircle,
  Plus,
  RefreshCw,
  ShieldAlert,
  TriangleAlert,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
  buildExperimentCreatePayload,
  canManageExperiments,
  coreExperimentValidationError,
  createExperiment,
  deriveExperimentExperience,
  EXPERIMENT_CAUSALITY_DISCLAIMER,
  EXPERIMENT_SCOPE_ORGANIZATION,
  EXPERIMENT_SCOPE_QUEUE,
  EXPERIMENT_STATUS_ARCHIVED,
  EXPERIMENT_STATUS_CANCELLED,
  EXPERIMENT_STATUS_COMPLETED,
  EXPERIMENT_WINDOW_DAYS,
  experimentActions,
  experimentMeasurementStatusLabel,
  experimentMetricDefinition,
  experimentScopeLabel,
  experimentStatusHint,
  fetchExperimentList,
  fetchServiceQueues,
  formatExperimentWindowDays,
  formatMetricCell,
  formatTimestamp,
  isExperimentPrimaryAction,
  runExperimentAction,
  experimentTargetableMetrics,
  type ExperimentLifecycleAction,
  type ExperimentMetricCategory,
  type ServiceQueue,
  type ServiceTransformationExperiment,
} from "@/lib/experiments/experiments";
import {
  fetchSimulationList,
  formatWindowLabel,
  SIMULATION_STATUS_EVALUATED,
  type ServiceTransformationScenario,
} from "@/lib/simulation/simulation";

const METRIC_CATEGORY_LABELS: Record<ExperimentMetricCategory, string> = {
  "ai-adoption": "AI adoption",
  "service-quality": "Service quality & operations",
  value: "Value",
};

const DIRECTION_OPTIONS = [
  { value: "", label: "Not set" },
  { value: "increase", label: "Increase" },
  { value: "decrease", label: "Decrease" },
];

function ExperimentActionButton({
  action,
  busy,
  onClick,
}: {
  action: ExperimentLifecycleAction;
  busy: boolean;
  onClick: () => void;
}) {
  const primary = isExperimentPrimaryAction(action);
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={
        primary
          ? "flex h-9 items-center gap-2 rounded-xl bg-violet-600 px-3 text-xs font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
          : "flex h-9 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:border-violet-300 hover:text-violet-700 disabled:opacity-50"
      }
    >
      {busy && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
      {actionLabel(action)}
    </button>
  );
}

function actionLabel(action: ExperimentLifecycleAction): string {
  switch (action) {
    case "capture-baseline":
      return "Capture baseline";
    case "start":
      return "Start";
    case "complete":
      return "Complete & measure";
    case "cancel":
      return "Cancel";
    case "archive":
      return "Archive";
  }
}

function ExperimentCard({
  experiment,
  canManage,
  busyId,
  onAction,
}: {
  experiment: ServiceTransformationExperiment;
  canManage: boolean;
  busyId: number | null;
  onAction: (experimentId: number, action: ExperimentLifecycleAction) => void;
}) {
  const archived = experiment.status === EXPERIMENT_STATUS_ARCHIVED;
  const busy = busyId === experiment.id;
  const hint = experimentStatusHint(experiment.status);
  const actions = canManage ? experimentActions(experiment.status) : [];

  const targetSummary = Object.entries(experiment.target_metrics)
    .map(([key, value]) => {
      const definition = experimentMetricDefinition(key);
      const label = definition?.shortLabel ?? key;
      return `${label} ${formatMetricCell(value, definition?.kind ?? "count")}`;
    })
    .join(", ");

  return (
    <div
      className={`rounded-[20px] border p-5 transition ${
        archived
          ? "border-slate-200 bg-slate-50/60 opacity-75"
          : "border-slate-200/80 bg-white/80"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <Link
            href={`/experiments/${experiment.id}`}
            className="text-[15px] font-medium text-slate-900 transition hover:text-violet-700"
          >
            {experiment.name}
          </Link>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <ExperimentStatusBadge status={experiment.status} />
            <Badge>{experimentScopeLabel(experiment.scope_type, experiment.scope_key)}</Badge>
            <Badge variant="info">
              {formatExperimentWindowDays(experiment.baseline_window_days)} →
              {formatExperimentWindowDays(experiment.measurement_window_days)}
            </Badge>
            {experiment.measurement_status &&
              experiment.status === EXPERIMENT_STATUS_COMPLETED && (
                <Badge variant="violet">
                  {experimentMeasurementStatusLabel(experiment.measurement_status)}
                </Badge>
              )}
          </div>
        </div>

        {actions.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            {actions.map((action) => (
              <ExperimentActionButton
                key={action}
                action={action}
                busy={busy}
                onClick={() => onAction(experiment.id, action)}
              />
            ))}
          </div>
        )}
      </div>

      {hint && (
        <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-slate-500">
          <span className="font-medium text-slate-400">State:</span>
          {hint}
        </p>
      )}

      {experiment.description && (
        <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">
          {experiment.description}
        </p>
      )}

      <div className="mt-4 grid gap-2 text-xs text-slate-500 sm:grid-cols-2">
        <div>
          <span className="font-medium text-slate-400">Targets:</span>{" "}
          {targetSummary || "—"}
        </div>
        <div>
          <span className="font-medium text-slate-400">Created:</span>{" "}
          {formatTimestamp(experiment.created_at)}
        </div>
        {experiment.created_by_subject && (
          <div className="sm:col-span-2">
            <span className="font-medium text-slate-400">Created by:</span>{" "}
            {experiment.created_by_subject}
          </div>
        )}
      </div>
    </div>
  );
}

interface ExperimentalFormState {
  name: string;
  description: string;
  scopeType: string;
  scopeKey: string;
  baselineWindowDays: number;
  measurementWindowDays: number;
  plannedStartAt: string;
  plannedEndAt: string;
  hypothesisSummary: string;
  changeDescription: string;
  targetInputs: Record<string, string>;
  directions: Record<string, string>;
  sourceScenarioId: string;
}

function TargetMetricControl({
  definitionKey,
  definitionLabel,
  definitionKind,
  definitionHelper,
  value,
  direction,
  onValueChange,
  onDirectionChange,
}: {
  definitionKey: string;
  definitionLabel: string;
  definitionKind: string;
  definitionHelper: string;
  value: string;
  direction: string;
  onValueChange: (value: string) => void;
  onDirectionChange: (direction: string) => void;
}) {
  const fieldId = `experiment-target-${definitionKey}`;
  const directionId = `experiment-direction-${definitionKey}`;
  const unit = definitionKind === "rate" || definitionKind === "roi" ? "%" : definitionKind === "minutes" ? "min" : "";
  return (
    <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label
          htmlFor={fieldId}
          className="text-sm font-medium text-slate-800"
        >
          {definitionLabel}
        </label>
        <div className="flex items-center gap-2">
          <input
            id={fieldId}
            aria-label={`${definitionLabel} target value`}
            type="number"
            step={definitionKind === "count" ? 1 : 0.1}
            value={value}
            onChange={(event) => onValueChange(event.target.value)}
            className="h-9 w-28 rounded-xl border border-slate-200 bg-white px-3 text-right text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          />
          {unit && <span className="text-sm text-slate-500">{unit}</span>}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs leading-5 text-slate-400">{definitionHelper}</p>
        <select
          id={directionId}
          aria-label={`${definitionLabel} expected direction`}
          value={direction}
          onChange={(event) => onDirectionChange(event.target.value)}
          className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-600 focus:border-violet-300 focus:outline-none"
        >
          {DIRECTION_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function CreateExperimentForm({
  onCreated,
  onCancel,
}: {
  onCreated: (experiment: ServiceTransformationExperiment) => void;
  onCancel: () => void;
}) {
  const { refresh } = useAuthorization();
  const [form, setForm] = useState<ExperimentalFormState>({
    name: "",
    description: "",
    scopeType: EXPERIMENT_SCOPE_ORGANIZATION,
    scopeKey: "",
    baselineWindowDays: 30,
    measurementWindowDays: 30,
    plannedStartAt: "",
    plannedEndAt: "",
    hypothesisSummary: "",
    changeDescription: "",
    targetInputs: {},
    directions: {},
    sourceScenarioId: "",
  });
  const [queues, setQueues] = useState<ServiceQueue[]>([]);
  const [scenarios, setScenarios] = useState<ServiceTransformationScenario[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void fetchServiceQueues().then((list) => {
      if (!cancelled) setQueues(list);
    });
    void fetchSimulationList().then((list) => {
      if (!cancelled)
        setScenarios(
          list.filter(
            (scenario) => scenario.status === SIMULATION_STATUS_EVALUATED,
          ),
        );
    });
    return () => {
      cancelled = true;
    };
  }, []);

  function update<K extends keyof ExperimentalFormState>(
    key: K,
    value: ExperimentalFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  const targetable = useMemo(
    () => experimentTargetableMetrics(form.scopeType),
    [form.scopeType],
  );

  const targetMetrics = useMemo(() => {
    const result: Record<string, number> = {};
    for (const definition of targetable) {
      const raw = form.targetInputs[definition.key];
      if (raw === undefined || raw === null || raw.trim() === "") {
        continue;
      }
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) {
        result[definition.key] = parsed;
      }
    }
    return result;
  }, [form.targetInputs, targetable]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();

    const validationError = coreExperimentValidationError({
      name: form.name,
      summary: form.hypothesisSummary,
      targetMetrics,
      scopeType: form.scopeType,
    });
    if (validationError) {
      setError(validationError);
      return;
    }

    setCreating(true);
    setError("");
    try {
      const experiment = await createExperiment(
        buildExperimentCreatePayload({
          name: form.name,
          description: form.description,
          scopeType: form.scopeType,
          scopeKey: form.scopeKey.trim(),
          baselineWindowDays: form.baselineWindowDays,
          measurementWindowDays: form.measurementWindowDays,
          plannedStartAt: form.plannedStartAt || null,
          plannedEndAt: form.plannedEndAt || null,
          hypothesisSummary: form.hypothesisSummary,
          changeDescription: form.changeDescription,
          expectedDirection: form.directions,
          targetMetrics,
          sourceScenarioId: form.sourceScenarioId
            ? Number(form.sourceScenarioId)
            : null,
        }),
      );
      onCreated(experiment);
    } catch (err) {
      if (err instanceof Object && "status" in err && "detail" in err) {
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
        err instanceof Error ? err.message : "Could not create the experiment.",
      );
    } finally {
      setCreating(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="app-panel rounded-[22px] p-6 md:p-7"
      aria-label="Create transformation experiment"
    >
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium text-slate-900">Create experiment</h2>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            Define the scope, windows, hypothesis, and target metrics. The
            baseline is captured after creation; the observed outcome is
            measured when you complete the experiment.
          </p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:text-slate-800"
          aria-label="Close create experiment form"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="mb-6 grid gap-4 md:grid-cols-2">
        <div>
          <label
            htmlFor="experiment-name"
            className="mb-1.5 block text-xs font-medium text-slate-700"
          >
            Experiment name
          </label>
          <input
            id="experiment-name"
            type="text"
            maxLength={255}
            value={form.name}
            onChange={(event) => update("name", event.target.value)}
            placeholder="e.g. Autonomous dispatch rollout"
            className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          />
        </div>

        <div>
          <label
            htmlFor="experiment-scope"
            className="mb-1.5 block text-xs font-medium text-slate-700"
          >
            Scope
          </label>
          <select
            id="experiment-scope"
            value={form.scopeType}
            onChange={(event) => update("scopeType", event.target.value)}
            className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          >
            <option value={EXPERIMENT_SCOPE_ORGANIZATION}>Organization</option>
            <option value={EXPERIMENT_SCOPE_QUEUE}>Queue</option>
          </select>
        </div>

        {form.scopeType === EXPERIMENT_SCOPE_QUEUE && (
          <div className="md:col-span-2">
            <label
              htmlFor="experiment-queue"
              className="mb-1.5 block text-xs font-medium text-slate-700"
            >
              Queue
            </label>
            <select
              id="experiment-queue"
              value={form.scopeKey}
              onChange={(event) => update("scopeKey", event.target.value)}
              className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
            >
              <option value="">Select a queue…</option>
              {queues.map((queue) => (
                <option key={queue.id} value={queue.key}>
                  {queue.name}
                </option>
              ))}
            </select>
          </div>
        )}

        <div>
          <label
            htmlFor="experiment-baseline-window"
            className="mb-1.5 block text-xs font-medium text-slate-700"
          >
            Baseline window
          </label>
          <select
            id="experiment-baseline-window"
            value={form.baselineWindowDays}
            onChange={(event) =>
              update("baselineWindowDays", Number(event.target.value))
            }
            className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          >
            {EXPERIMENT_WINDOW_DAYS.map((option) => (
              <option key={option} value={option}>
                {formatWindowLabel(option)}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="experiment-measurement-window"
            className="mb-1.5 block text-xs font-medium text-slate-700"
          >
            Measurement window
          </label>
          <select
            id="experiment-measurement-window"
            value={form.measurementWindowDays}
            onChange={(event) =>
              update("measurementWindowDays", Number(event.target.value))
            }
            className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          >
            {EXPERIMENT_WINDOW_DAYS.map((option) => (
              <option key={option} value={option}>
                {formatWindowLabel(option)}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="experiment-planned-start"
            className="mb-1.5 block text-xs font-medium text-slate-700"
          >
            Planned start{" "}
            <span className="font-normal text-slate-400">(optional)</span>
          </label>
          <input
            id="experiment-planned-start"
            type="datetime-local"
            value={form.plannedStartAt}
            onChange={(event) => update("plannedStartAt", event.target.value)}
            className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          />
        </div>

        <div>
          <label
            htmlFor="experiment-planned-end"
            className="mb-1.5 block text-xs font-medium text-slate-700"
          >
            Planned end{" "}
            <span className="font-normal text-slate-400">(optional)</span>
          </label>
          <input
            id="experiment-planned-end"
            type="datetime-local"
            value={form.plannedEndAt}
            onChange={(event) => update("plannedEndAt", event.target.value)}
            className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          />
        </div>
      </div>

      <div className="mb-6 grid gap-4">
        <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label
                htmlFor="experiment-hypothesis"
                className="mb-1.5 block text-xs font-medium text-slate-700"
              >
                Hypothesis summary
              </label>
              <input
                id="experiment-hypothesis"
                type="text"
                maxLength={500}
                value={form.hypothesisSummary}
                onChange={(event) =>
                  update("hypothesisSummary", event.target.value)
                }
                placeholder="e.g. Enabling low-risk autonomous dispatch raises autonomous execution without raising reopens."
                className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
              />
            </div>

          <div>
            <label
              htmlFor="experiment-change"
              className="mb-1.5 block text-xs font-medium text-slate-700"
            >
              Change description{" "}
              <span className="font-normal text-slate-400">(optional)</span>
            </label>
            <textarea
              id="experiment-change"
              maxLength={2000}
              rows={2}
              value={form.changeDescription}
              onChange={(event) =>
                update("changeDescription", event.target.value)
              }
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
            />
          </div>
        </div>

        <div>
          <label
            htmlFor="experiment-description"
            className="mb-1.5 block text-xs font-medium text-slate-700"
          >
            Description{" "}
            <span className="font-normal text-slate-400">(optional)</span>
          </label>
          <textarea
            id="experiment-description"
            maxLength={2000}
            rows={2}
            value={form.description}
            onChange={(event) => update("description", event.target.value)}
            className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
          />
        </div>
      </div>

      <div className="mb-6">
        <h3 className="mb-3 text-sm font-medium text-slate-900">
          Target metrics{" "}
          <span className="font-normal text-slate-400">
            — set at least one
          </span>
        </h3>
        <div className="space-y-3">
          {(Object.keys(METRIC_CATEGORY_LABELS) as ExperimentMetricCategory[]).map(
            (category) => {
              const definitions = targetable.filter(
                (definition) => definition.category === category,
              );
              if (definitions.length === 0) {
                return null;
              }
              return (
                <div key={category}>
                  <p className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-400">
                    <ClipboardCheck className="h-3.5 w-3.5" />
                    {METRIC_CATEGORY_LABELS[category]}
                  </p>
                  <div className="grid gap-3 md:grid-cols-2">
                    {definitions.map((definition) => (
                      <TargetMetricControl
                        key={definition.key}
                        definitionKey={definition.key}
                        definitionLabel={definition.label}
                        definitionKind={definition.kind}
                        definitionHelper={definition.helper}
                        value={form.targetInputs[definition.key] ?? ""}
                        direction={form.directions[definition.key] ?? ""}
                        onValueChange={(value) =>
                          update("targetInputs", {
                            ...form.targetInputs,
                            [definition.key]: value,
                          })
                        }
                        onDirectionChange={(direction) =>
                          update("directions", {
                            ...form.directions,
                            [definition.key]: direction,
                          })
                        }
                      />
                    ))}
                  </div>
                </div>
              );
            },
          )}
        </div>
        {form.scopeType === EXPERIMENT_SCOPE_QUEUE && (
          <p className="mt-2 text-xs leading-5 text-slate-400">
            Queue-scoped experiments cannot target Value or ROI metrics because
            autonomous-execution economics are not attributable to a single
            queue.
          </p>
        )}
      </div>

      <div className="mb-6">
        <label
          htmlFor="experiment-scenario"
          className="mb-1.5 block text-xs font-medium text-slate-700"
        >
          Source projection{" "}
          <span className="font-normal text-slate-400">(optional)</span>
        </label>
        <select
          id="experiment-scenario"
          value={form.sourceScenarioId}
          onChange={(event) => update("sourceScenarioId", event.target.value)}
          className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 focus:border-violet-300 focus:outline-none"
        >
          <option value="">No simulation link</option>
          {scenarios.map((scenario) => (
            <option key={scenario.id} value={scenario.id}>
              {scenario.name} ({formatWindowLabel(scenario.window_days)})
            </option>
          ))}
        </select>
        <p className="mt-1.5 text-xs leading-5 text-slate-400">
          This links the experiment to an evaluated simulation scenario for
          later variance comparison. It does not apply the scenario to
          production. Only evaluated scenarios are selectable.
        </p>
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
            <Plus className="h-4 w-4" />
          )}
          {creating ? "Creating…" : "Create experiment"}
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

type LibraryFilter = "all" | "active" | "completed" | "cancelled" | "archived";

const FILTER_OPTIONS: readonly { id: LibraryFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "completed", label: "Completed" },
  { id: "cancelled", label: "Cancelled" },
  { id: "archived", label: "Archived" },
];

function experimentMatchesFilter(
  experiment: ServiceTransformationExperiment,
  filter: LibraryFilter,
): boolean {
  switch (filter) {
    case "all":
      return true;
    case "active":
      return (
        experiment.status !== EXPERIMENT_STATUS_COMPLETED &&
        experiment.status !== EXPERIMENT_STATUS_CANCELLED &&
        experiment.status !== EXPERIMENT_STATUS_ARCHIVED
      );
    case "completed":
      return experiment.status === EXPERIMENT_STATUS_COMPLETED;
    case "cancelled":
      return experiment.status === EXPERIMENT_STATUS_CANCELLED;
    case "archived":
      return experiment.status === EXPERIMENT_STATUS_ARCHIVED;
  }
}

export default function ExperimentsPage() {
  const router = useRouter();
  const { can, refresh } = useAuthorization();
  const experience = deriveExperimentExperience(can);
  const canManage = canManageExperiments(can);

  const [experiments, setExperiments] = useState<
    ServiceTransformationExperiment[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [filter, setFilter] = useState<LibraryFilter>("all");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState("");

  const loadExperiments = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const list = await fetchExperimentList();
      setExperiments(list);
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
          : "Could not load transformation experiments.",
      );
    } finally {
      setLoading(false);
    }
  }, [refresh]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadExperiments();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadExperiments]);

  const visibleExperiments = useMemo(
    () =>
      experiments
        .filter((experiment) => experimentMatchesFilter(experiment, filter))
        .sort(
          (a, b) =>
            new Date(b.updated_at).getTime() -
            new Date(a.updated_at).getTime(),
        ),
    [experiments, filter],
  );

  const counts = useMemo(() => {
    const result: Record<LibraryFilter, number> = {
      all: experiments.length,
      active: 0,
      completed: 0,
      cancelled: 0,
      archived: 0,
    };
    for (const experiment of experiments) {
      if (experiment.status === EXPERIMENT_STATUS_COMPLETED) {
        result.completed += 1;
      } else if (experiment.status === EXPERIMENT_STATUS_CANCELLED) {
        result.cancelled += 1;
      } else if (experiment.status === EXPERIMENT_STATUS_ARCHIVED) {
        result.archived += 1;
      } else {
        result.active += 1;
      }
    }
    return result;
  }, [experiments]);

  const handleAction = useCallback(
    async (experimentId: number, action: ExperimentLifecycleAction) => {
      setBusyId(experimentId);
      setActionError("");
      try {
        const experiment = await runExperimentAction(experimentId, action);
        await loadExperiments();
        if (action === "capture-baseline" || action === "start" || action === "complete") {
          router.push(`/experiments/${experiment.id}`);
        }
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
        setBusyId(null);
      }
    },
    [loadExperiments, refresh, router],
  );

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="no-print fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between gap-4 px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Transformation Experiments
              </p>
              <p className="hidden text-[11px] text-slate-400 sm:block">
                Measure observed service outcomes against an immutable baseline,
                explicit targets, and optional simulation projections.
              </p>
            </div>

            <div className="flex items-center gap-3">
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
                  {createOpen ? "Close" : "Create experiment"}
                </button>
              )}
            </div>
          </div>
        </header>

        <main className="print-area mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Experiment executive workspace
            </p>
            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Transformation experiments,
              <span className="gradient-text"> measured against real baselines.</span>
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Track a planned change through its lifecycle — capture an
              immutable baseline, run the measurement window, and compare the
              observed outcome against explicit targets and optional simulation
              projections.
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
                    You can browse and open experiments in this workspace.
                    Creating experiments and lifecycle actions (capture
                    baseline, start, complete &amp; measure, cancel, archive)
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
                  Observed change is not cause
                </p>
                <p className="mt-1 max-w-3xl text-xs leading-5 text-amber-700/80">
                  {EXPERIMENT_CAUSALITY_DISCLAIMER} The workspace reports
                  measured differences as numbers only and never infers that an
                  intervention caused an observed improvement.
                </p>
              </div>
            </div>
          </section>

          {createOpen && canManage && (
            <section className="mb-8">
              <CreateExperimentForm
                onCreated={(experiment) => {
                  setCreateOpen(false);
                  void loadExperiments();
                  router.push(`/experiments/${experiment.id}`);
                }}
                onCancel={() => setCreateOpen(false)}
              />
            </section>
          )}

          <section className="mb-6 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-slate-900">
                Experiment library
              </h2>
              <Badge variant="violet">{counts[filter]}</Badge>
            </div>

            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 rounded-full border border-slate-200 bg-white/80 p-1 text-[11px] text-slate-500 shadow-sm">
                {FILTER_OPTIONS.map((option) => (
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
                onClick={() => void loadExperiments()}
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh experiments"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </button>
            </div>
          </section>

          {loadError ? (
            <div className="my-10 flex flex-col items-center gap-3 rounded-[20px] border border-rose-200 bg-rose-50/60 p-10 text-center">
              <TriangleAlert className="h-6 w-6 text-rose-500" />
              <p className="text-sm text-rose-700">{loadError}</p>
              <button
                type="button"
                onClick={() => void loadExperiments()}
                className="rounded-xl border border-rose-200 bg-white px-4 py-2 text-xs font-medium text-rose-700 transition hover:bg-rose-50"
              >
                Try again
              </button>
            </div>
          ) : loading && experiments.length === 0 ? (
            <div className="my-10 flex items-center justify-center gap-3 rounded-[20px] border border-slate-200 bg-white/70 p-10">
              <LoaderCircle className="h-5 w-5 animate-spin text-violet-500" />
              <p className="text-sm text-slate-500">
                Loading transformation experiments…
              </p>
            </div>
          ) : visibleExperiments.length === 0 ? (
            <div className="my-10 rounded-[20px] border border-slate-200 bg-white/70 p-10 text-center">
              <FlaskConical className="mx-auto h-6 w-6 text-slate-300" />
              <p className="mt-3 text-sm font-medium text-slate-700">
                {experiments.length === 0
                  ? "No transformation experiments yet."
                  : "No experiments match this filter."}
              </p>
              <p className="mx-auto mt-1 max-w-md text-xs leading-5 text-slate-500">
                Create an experiment with a hypothesis and explicit target
                metrics, capture an immutable baseline, then run the measurement
                window and let the outcome engine measure what changed.
              </p>
              {canManage && experiments.length === 0 && (
                <button
                  type="button"
                  onClick={() => setCreateOpen(true)}
                  className="mt-4 flex h-10 items-center gap-2 rounded-xl bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700"
                >
                  <Plus className="h-4 w-4" />
                  Create experiment
                </button>
              )}
            </div>
          ) : (
            <section className="grid gap-4 xl:grid-cols-2">
              {visibleExperiments.map((experiment) => (
                <ExperimentCard
                  key={experiment.id}
                  experiment={experiment}
                  canManage={canManage}
                  busyId={busyId}
                  onAction={(experimentId, action) =>
                    void handleAction(experimentId, action)
                  }
                />
              ))}
            </section>
          )}

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