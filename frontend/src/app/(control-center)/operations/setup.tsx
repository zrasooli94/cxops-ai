"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { ServiceQueue, SLAPolicy } from "./types.ts";
import {
  automationActionSummary,
  SLA_EVENT_TYPES,
  validateAutomationRule,
  type AutomationAction,
  type AutomationRule,
  type AutomationRuleInput,
} from "@/lib/operations/escalations.ts";

export default function Setup({
  queues,
  slaPolicies,
  onSaved,
  canManageAutomation,
}: {
  queues: ServiceQueue[];
  slaPolicies: SLAPolicy[];
  onSaved: () => void;
  canManageAutomation: boolean;
}) {
  const [activeTab, setActiveTab] = useState<"queues" | "sla" | "escalations">("queues");

  return (
    <div className="space-y-6">
      <div className="flex border-b border-slate-100">
        <button
          onClick={() => setActiveTab("queues")}
          className={`px-6 py-3 text-sm font-medium ${
            activeTab === "queues"
              ? "border-b-2 border-violet-500 text-violet-600"
              : "text-slate-500"
          }`}
        >
          Service Queues
        </button>
        <button
          onClick={() => setActiveTab("sla")}
          className={`px-6 py-3 text-sm font-medium ${
            activeTab === "sla"
              ? "border-b-2 border-violet-500 text-violet-600"
              : "text-slate-500"
          }`}
        >
          SLA Policies
        </button>
        {canManageAutomation && (
          <button
            onClick={() => setActiveTab("escalations")}
            className={`px-6 py-3 text-sm font-medium ${
              activeTab === "escalations"
                ? "border-b-2 border-violet-500 text-violet-600"
                : "text-slate-500"
            }`}
          >
            Escalation Automation
          </button>
        )}
      </div>

      {activeTab === "queues" && (
        <QueueConfig queues={queues} slaPolicies={slaPolicies} onSaved={onSaved} />
      )}
      {activeTab === "sla" && <SLAConfig slaPolicies={slaPolicies} onSaved={onSaved} />}
      {activeTab === "escalations" && canManageAutomation && (
        <EscalationAutomationConfig />
      )}
    </div>
  );
}

function QueueConfig({
  queues,
  slaPolicies,
  onSaved,
}: {
  queues: ServiceQueue[];
  slaPolicies: SLAPolicy[];
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    key: "",
    name: "",
    description: "",
    active: true,
    is_default: false,
    sla_policy_id: "",
  });
  const [editing, setEditing] = useState<ServiceQueue | null>(null);
  const [error, setError] = useState("");

  const reset = () => {
    setForm({
      key: "",
      name: "",
      description: "",
      active: true,
      is_default: false,
      sla_policy_id: "",
    });
    setEditing(null);
  };

  const save = async () => {
    setError("");
    const payload = {
      ...form,
      sla_policy_id: form.sla_policy_id ? Number(form.sla_policy_id) : null,
    };

    try {
      const url = editing
        ? `/api/backend/service-queues/${editing.id}`
        : "/api/backend/service-queues";
      const method = editing ? "PATCH" : "POST";

      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Save failed");
      }

      reset();
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  };

  return (
    <div className="space-y-4">
      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="grid gap-3">
        <input
          placeholder="Key (machine name)"
          value={form.key}
          onChange={(e) => setForm((f) => ({ ...f, key: e.target.value }))}
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />
        <input
          placeholder="Display name"
          value={form.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />
        <input
          placeholder="Description"
          value={form.description}
          onChange={(e) =>
            setForm((f) => ({ ...f, description: e.target.value }))
          }
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />
        <select
          value={form.sla_policy_id}
          onChange={(e) =>
            setForm((f) => ({ ...f, sla_policy_id: e.target.value }))
          }
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        >
          <option value="">No SLA policy</option>
          {slaPolicies.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.active}
            onChange={(e) =>
              setForm((f) => ({ ...f, active: e.target.checked }))
            }
          />
          Active
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.is_default}
            onChange={(e) =>
              setForm((f) => ({ ...f, is_default: e.target.checked }))
            }
          />
          Default queue
        </label>
      </div>

      <button
        onClick={save}
        className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
      >
        {editing ? "Update queue" : "Create queue"}
      </button>

      <div className="mt-6 space-y-2">
        {queues.map((q) => (
          <div
            key={q.id}
            className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-3"
          >
            <div>
              <p className="font-medium text-slate-900">
                {q.name}{" "}
                <span className="text-xs font-normal text-slate-400">
                  ({q.key})
                </span>
              </p>
              <p className="text-xs text-slate-500">
                {q.active ? "Active" : "Inactive"}
                {q.is_default ? " · Default" : ""}
              </p>
            </div>
            <button
              onClick={() => {
                setEditing(q);
                setForm({
                  key: q.key,
                  name: q.name,
                  description: "",
                  active: q.active,
                  is_default: q.is_default,
                  sla_policy_id: q.sla_policy_id?.toString() ?? "",
                });
              }}
              className="text-sm text-violet-600 hover:underline"
            >
              Edit
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function SLAConfig({
  slaPolicies,
  onSaved,
}: {
  slaPolicies: SLAPolicy[];
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    name: "",
    enabled: true,
    is_default: false,
    first_response_low_minutes: 240,
    first_response_normal_minutes: 120,
    first_response_high_minutes: 60,
    first_response_urgent_minutes: 15,
    resolution_low_minutes: 2880,
    resolution_normal_minutes: 1440,
    resolution_high_minutes: 480,
    resolution_urgent_minutes: 120,
  });
  const [editing, setEditing] = useState<SLAPolicy | null>(null);
  const [error, setError] = useState("");

  const reset = () => {
    setForm({
      name: "",
      enabled: true,
      is_default: false,
      first_response_low_minutes: 240,
      first_response_normal_minutes: 120,
      first_response_high_minutes: 60,
      first_response_urgent_minutes: 15,
      resolution_low_minutes: 2880,
      resolution_normal_minutes: 1440,
      resolution_high_minutes: 480,
      resolution_urgent_minutes: 120,
    });
    setEditing(null);
  };

  const save = async () => {
    setError("");
    try {
      const url = editing
        ? `/api/backend/sla-policies/${editing.id}`
        : "/api/backend/sla-policies";
      const method = editing ? "PATCH" : "POST";

      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Save failed");
      }

      reset();
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  };

  const minutesInput = (
    label: string,
    value: number,
    onChange: (v: number) => void
  ) => (
    <label key={label} className="block text-sm">
      <span className="text-slate-600">{label}</span>
      <input
        type="number"
        min={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
      />
    </label>
  );

  return (
    <div className="space-y-4">
      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="grid gap-3">
        <input
          placeholder="Policy name"
          value={form.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) =>
              setForm((f) => ({ ...f, enabled: e.target.checked }))
            }
          />
          Enabled
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.is_default}
            onChange={(e) =>
              setForm((f) => ({ ...f, is_default: e.target.checked }))
            }
          />
          Default policy
        </label>

        <div className="mt-2 grid grid-cols-2 gap-3">
          {minutesInput(
            "First response: Low",
            form.first_response_low_minutes,
            (v) => setForm((f) => ({ ...f, first_response_low_minutes: v }))
          )}
          {minutesInput(
            "First response: Normal",
            form.first_response_normal_minutes,
            (v) => setForm((f) => ({ ...f, first_response_normal_minutes: v }))
          )}
          {minutesInput(
            "First response: High",
            form.first_response_high_minutes,
            (v) => setForm((f) => ({ ...f, first_response_high_minutes: v }))
          )}
          {minutesInput(
            "First response: Urgent",
            form.first_response_urgent_minutes,
            (v) => setForm((f) => ({ ...f, first_response_urgent_minutes: v }))
          )}
          {minutesInput(
            "Resolution: Low",
            form.resolution_low_minutes,
            (v) => setForm((f) => ({ ...f, resolution_low_minutes: v }))
          )}
          {minutesInput(
            "Resolution: Normal",
            form.resolution_normal_minutes,
            (v) => setForm((f) => ({ ...f, resolution_normal_minutes: v }))
          )}
          {minutesInput(
            "Resolution: High",
            form.resolution_high_minutes,
            (v) => setForm((f) => ({ ...f, resolution_high_minutes: v }))
          )}
          {minutesInput(
            "Resolution: Urgent",
            form.resolution_urgent_minutes,
            (v) => setForm((f) => ({ ...f, resolution_urgent_minutes: v }))
          )}
        </div>
      </div>

      <button
        onClick={save}
        className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
      >
        {editing ? "Update policy" : "Create policy"}
      </button>

      <div className="mt-6 space-y-2">
        {slaPolicies.map((p) => (
          <div
            key={p.id}
            className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-3"
          >
            <div>
              <p className="font-medium text-slate-900">{p.name}</p>
              <p className="text-xs text-slate-500">
                {p.enabled ? "Enabled" : "Disabled"}
                {p.is_default ? " · Default" : ""}
              </p>
            </div>
            <button
              onClick={() => {
                setEditing(p);
                setForm({ ...p });
              }}
              className="text-sm text-violet-600 hover:underline"
            >
              Edit
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function EscalationAutomationConfig() {
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [form, setForm] = useState<AutomationRuleInput>({
    name: "",
    event_type: "sla.first_response.due_soon",
    enabled: true,
    actions: {},
  });
  const [editing, setEditing] = useState<AutomationRule | null>(null);

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/backend/automation-rules", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Failed to load automation rules");
      const data: AutomationRule[] = await response.json();
      setRules(
        data.filter((rule) => SLA_EVENT_TYPES.includes(rule.event_type))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Automation rules error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    void loadRules();
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [loadRules]);

  const reset = () => {
    setForm({
      name: "",
      event_type: "sla.first_response.due_soon",
      enabled: true,
      actions: {},
    });
    setEditing(null);
  };

  const updateAction = (patch: Partial<AutomationAction>) => {
    setForm((f) => ({
      ...f,
      actions: { ...f.actions, ...patch },
    }));
  };

  const save = async () => {
    setError("");
    const validationError = validateAutomationRule(form);
    if (validationError) {
      setError(validationError);
      return;
    }

    try {
      const url = editing
        ? `/api/backend/automation-rules/${editing.id}`
        : "/api/backend/automation-rules";
      const method = editing ? "PATCH" : "POST";

      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Save failed");
      }

      reset();
      void loadRules();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  };

  const remove = async (id: number) => {
    if (!window.confirm("Delete this automation rule?")) return;
    setError("");
    try {
      const response = await fetch(`/api/backend/automation-rules/${id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Delete failed");
      }
      void loadRules();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const eventTypeLabel = useMemo(() => {
    const map: Record<string, string> = {
      "sla.first_response.due_soon": "First response due soon",
      "sla.first_response.breached": "First response breached",
      "sla.resolution.due_soon": "Resolution due soon",
      "sla.resolution.breached": "Resolution breached",
    };
    return (event: string) => map[event] ?? event;
  }, []);

  return (
    <div className="space-y-4">
      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="grid gap-3">
        <input
          placeholder="Rule name"
          value={form.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />

        <select
          value={form.event_type}
          onChange={(e) =>
            setForm((f) => ({ ...f, event_type: e.target.value }))
          }
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        >
          {SLA_EVENT_TYPES.map((event) => (
            <option key={event} value={event}>
              {eventTypeLabel(event)}
            </option>
          ))}
        </select>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) =>
              setForm((f) => ({ ...f, enabled: e.target.checked }))
            }
          />
          Enabled
        </label>

        <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
          Actions
        </p>

        <select
          value={form.actions.priority ?? ""}
          onChange={(e) =>
            updateAction({
              priority: e.target.value || undefined,
            })
          }
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        >
          <option value="">No priority change</option>
          <option value="low">Low</option>
          <option value="normal">Normal</option>
          <option value="high">High</option>
          <option value="urgent">Urgent</option>
        </select>

        <input
          placeholder="Queue key (optional)"
          value={form.actions.service_queue_key ?? ""}
          onChange={(e) =>
            updateAction({
              service_queue_key: e.target.value || undefined,
            })
          }
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />

        <input
          placeholder="Category (optional)"
          value={form.actions.category ?? ""}
          onChange={(e) =>
            updateAction({
              category: e.target.value || undefined,
            })
          }
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />
      </div>

      <button
        onClick={save}
        className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
      >
        {editing ? "Update rule" : "Create rule"}
      </button>

      {loading && rules.length === 0 ? (
        <p className="text-sm text-slate-500">Loading rules…</p>
      ) : (
        <div className="mt-6 space-y-2">
          {rules.map((rule) => (
            <div
              key={rule.id}
              className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-3"
            >
              <div>
                <p className="font-medium text-slate-900">{rule.name}</p>
                <p className="text-xs text-slate-500">
                  {eventTypeLabel(rule.event_type)} ·{" "}
                  {automationActionSummary(rule.actions)} ·{" "}
                  {rule.enabled ? "Enabled" : "Disabled"}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => {
                    setEditing(rule);
                    setForm({
                      name: rule.name,
                      event_type: rule.event_type,
                      enabled: rule.enabled,
                      actions: { ...rule.actions },
                    });
                  }}
                  className="text-sm text-violet-600 hover:underline"
                >
                  Edit
                </button>
                <button
                  onClick={() => remove(rule.id)}
                  className="text-sm text-red-600 hover:underline"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
