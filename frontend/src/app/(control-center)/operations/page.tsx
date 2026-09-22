"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  LoaderCircle,
  RefreshCw,
  Search,
  Settings,
  User,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { CAPABILITIES } from "@/lib/authorization/capabilities";
import { useAuthorization } from "@/lib/authorization/context";

type SLAState = "not_configured" | "on_track" | "due_soon" | "breached" | "met";

type QueueSummary = {
  id: number;
  name: string;
  key: string;
  count: number;
};

type OperationsSummary = {
  open: number;
  unassigned: number;
  needs_response: number;
  response_breaches: number;
  resolution_breaches: number;
  due_soon: number;
  urgent: number;
  high: number;
  by_queue: QueueSummary[];
};

type AIRoutingSuggestion = {
  queue_id: number;
  queue_key: string;
  queue_name: string;
};

type OperationsQueueItem = {
  ticket_id: number;
  conversation_id: number | null;
  subject: string;
  status: string;
  priority: string;
  category: string | null;
  service_queue_id: number | null;
  service_queue_name: string | null;
  assigned_subject: string | null;
  is_assigned_to_me: boolean;
  created_at: string;
  updated_at: string;
  needs_response: boolean;
  first_response_due_at: string | null;
  first_response_at: string | null;
  first_response_sla_state: SLAState;
  resolution_due_at: string | null;
  resolved_at: string | null;
  resolution_sla_state: SLAState;
  overall_sla_state: SLAState;
  routing_source: string | null;
  ai_routing_suggestion: AIRoutingSuggestion | null;
};

type ServiceQueue = {
  id: number;
  key: string;
  name: string;
  active: boolean;
  is_default: boolean;
  sla_policy_id: number | null;
};

type SLAPolicy = {
  id: number;
  name: string;
  enabled: boolean;
  is_default: boolean;
  first_response_low_minutes: number;
  first_response_normal_minutes: number;
  first_response_high_minutes: number;
  first_response_urgent_minutes: number;
  resolution_low_minutes: number;
  resolution_normal_minutes: number;
  resolution_high_minutes: number;
  resolution_urgent_minutes: number;
};

function Badge({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?: "default" | "success" | "warning" | "danger" | "info";
}) {
  const styles = {
    default: "border-slate-200 bg-slate-50 text-slate-600",
    success: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning: "border-amber-200 bg-amber-50 text-amber-700",
    danger: "border-rose-200 bg-rose-50 text-rose-700",
    info: "border-blue-200 bg-blue-50 text-blue-700",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

function slaBadge(state: SLAState) {
  switch (state) {
    case "breached":
      return <Badge variant="danger">Breached</Badge>;
    case "due_soon":
      return <Badge variant="warning">Due soon</Badge>;
    case "met":
      return <Badge variant="success">Met</Badge>;
    case "on_track":
      return <Badge variant="success">On track</Badge>;
    default:
      return <Badge>Not configured</Badge>;
  }
}

function priorityVariant(priority: string) {
  switch (priority.toLowerCase()) {
    case "urgent":
      return "danger";
    case "high":
      return "warning";
    case "low":
      return "info";
    default:
      return "default";
  }
}

function formatAge(createdAt: string) {
  const minutes = Math.floor(
    (Date.now() - new Date(createdAt).getTime()) / 60000
  );
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export default function OperationsPage() {
  const { can } = useAuthorization();
  const canWrite = can(CAPABILITIES.TICKET_WRITE);
  const canManage = can(CAPABILITIES.AUTOMATION_MANAGE);

  const [summary, setSummary] = useState<OperationsSummary | null>(null);
  const [queue, setQueue] = useState<OperationsQueueItem[]>([]);
  const [queues, setQueues] = useState<ServiceQueue[]>([]);
  const [slaPolicies, setSlaPolicies] = useState<SLAPolicy[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [filters, setFilters] = useState({
    queue_id: "",
    status: "",
    priority: "",
    sla_state: "",
    needs_response: false,
    unassigned: false,
    mine: false,
    search: "",
  });

  const [showConfig, setShowConfig] = useState(false);
  const [activeConfigTab, setActiveConfigTab] = useState<"queues" | "sla">("queues");

  const loadSummary = useCallback(async () => {
    try {
      const response = await fetch("/api/backend/service-operations/summary", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Failed to load summary");
      setSummary(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Summary error");
    }
  }, []);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.queue_id) params.set("queue_id", filters.queue_id);
      if (filters.status) params.set("status", filters.status);
      if (filters.priority) params.set("priority", filters.priority);
      if (filters.sla_state) params.set("sla_state", filters.sla_state);
      if (filters.needs_response) params.set("needs_response", "true");
      if (filters.unassigned) params.set("unassigned", "true");
      if (filters.mine) params.set("mine", "true");
      if (filters.search) params.set("search", filters.search);

      const response = await fetch(
        `/api/backend/service-operations/queue?${params.toString()}`,
        { cache: "no-store" }
      );
      if (!response.ok) throw new Error("Failed to load queue");
      setQueue(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Queue error");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const loadConfig = useCallback(async () => {
    try {
      const [queuesResponse, slaResponse] = await Promise.all([
        fetch("/api/backend/service-queues", { cache: "no-store" }),
        fetch("/api/backend/sla-policies", { cache: "no-store" }),
      ]);
      if (queuesResponse.ok) setQueues(await queuesResponse.json());
      if (slaResponse.ok) setSlaPolicies(await slaResponse.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Config error");
    }
  }, []);

  useEffect(() => {
    // Initial data load on mount. The load helpers are memoized callbacks
    // that perform async fetches and update state; invoking them here is the
    // standard client-side hydration pattern for this page.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSummary();
    void loadConfig();
  }, [loadSummary, loadConfig]);

  useEffect(() => {
    const timer = setTimeout(() => void loadQueue(), 150);
    return () => clearTimeout(timer);
  }, [filters, loadQueue]);

  const claimTicket = async (ticketId: number) => {
    try {
      const response = await fetch(
        `/api/backend/service-operations/tickets/${ticketId}/claim`,
        { method: "POST" }
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Claim failed");
      }
      void loadQueue();
      void loadSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Claim failed");
    }
  };

  const applySuggestion = async (ticketId: number, queueId: number) => {
    try {
      const response = await fetch(
        `/api/backend/service-operations/tickets/${ticketId}/apply-suggestion`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ queue_id: queueId }),
        }
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Apply failed");
      }
      void loadQueue();
      void loadSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Apply failed");
    }
  };

  const assignQueue = async (ticketId: number, queueId: number | null) => {
    try {
      const response = await fetch(
        `/api/backend/tickets/${ticketId}/assignment`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ service_queue_id: queueId }),
        }
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Assignment failed");
      }
      void loadQueue();
      void loadSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Assignment failed");
    }
  };

  const summaryCards = useMemo(
    () => [
      { label: "Open", value: summary?.open ?? 0, icon: Users },
      { label: "Needs response", value: summary?.needs_response ?? 0, icon: AlertTriangle },
      { label: "Unassigned", value: summary?.unassigned ?? 0, icon: User },
      { label: "SLA breached", value: (summary?.response_breaches ?? 0) + (summary?.resolution_breaches ?? 0), icon: Clock },
      { label: "Due soon", value: summary?.due_soon ?? 0, icon: Clock },
    ],
    [summary]
  );

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
                Service Operations
              </p>
              <h1 className="mt-2 text-3xl font-light tracking-[-0.04em] text-slate-950">
                Operations
              </h1>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  void loadSummary();
                  void loadQueue();
                }}
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </button>
              {canManage && (
                <button
                  onClick={() => setShowConfig(true)}
                  className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600"
                >
                  <Settings className="h-4 w-4" />
                  Setup
                </button>
              )}
            </div>
          </div>
        </header>

        <div className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          {error && (
            <div className="mb-5 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
              {error}
              <button
                onClick={() => setError("")}
                className="ml-auto text-xs underline"
              >
                Dismiss
              </button>
            </div>
          )}

          <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {summaryCards.map((card) => (
              <div
                key={card.label}
                className="app-panel rounded-[20px] p-5"
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                    <card.icon className="h-5 w-5" />
                  </div>
                  <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                      {card.label}
                    </p>
                    <p className="editorial-number text-3xl font-medium tracking-[-0.045em] text-slate-950">
                      {card.value}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="mb-4 flex flex-wrap items-center gap-3">
            <select
              value={filters.queue_id}
              onChange={(e) =>
                setFilters((f) => ({ ...f, queue_id: e.target.value }))
              }
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
            >
              <option value="">All queues</option>
              {queues.map((q) => (
                <option key={q.id} value={q.id}>
                  {q.name}
                </option>
              ))}
            </select>

            <select
              value={filters.status}
              onChange={(e) =>
                setFilters((f) => ({ ...f, status: e.target.value }))
              }
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
            >
              <option value="">All statuses</option>
              <option value="new">New</option>
              <option value="open">Open</option>
              <option value="pending">Pending</option>
              <option value="solved">Solved</option>
              <option value="closed">Closed</option>
            </select>

            <select
              value={filters.priority}
              onChange={(e) =>
                setFilters((f) => ({ ...f, priority: e.target.value }))
              }
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
            >
              <option value="">All priorities</option>
              <option value="urgent">Urgent</option>
              <option value="high">High</option>
              <option value="normal">Normal</option>
              <option value="low">Low</option>
            </select>

            <select
              value={filters.sla_state}
              onChange={(e) =>
                setFilters((f) => ({ ...f, sla_state: e.target.value }))
              }
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
            >
              <option value="">All SLA states</option>
              <option value="breached">Breached</option>
              <option value="due_soon">Due soon</option>
              <option value="on_track">On track</option>
              <option value="met">Met</option>
              <option value="not_configured">Not configured</option>
            </select>

            <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
              <input
                type="checkbox"
                checked={filters.needs_response}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, needs_response: e.target.checked }))
                }
              />
              Needs response
            </label>

            <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
              <input
                type="checkbox"
                checked={filters.unassigned}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, unassigned: e.target.checked }))
                }
              />
              Unassigned
            </label>

            <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
              <input
                type="checkbox"
                checked={filters.mine}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, mine: e.target.checked }))
                }
              />
              Mine
            </label>

            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                placeholder="Search subject..."
                value={filters.search}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, search: e.target.value }))
                }
                className="rounded-xl border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm"
              />
            </div>
          </div>

          <div className="app-panel overflow-hidden rounded-[20px]">
            {loading && queue.length === 0 ? (
              <div className="flex h-64 items-center justify-center">
                <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
              </div>
            ) : queue.length === 0 ? (
              <div className="flex h-64 flex-col items-center justify-center text-slate-500">
                <CheckCircle2 className="mb-3 h-10 w-10 text-slate-300" />
                <p>No tickets match the current filters.</p>
              </div>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-5 py-3 font-medium">Ticket</th>
                    <th className="px-5 py-3 font-medium">Priority</th>
                    <th className="px-5 py-3 font-medium">Queue</th>
                    <th className="px-5 py-3 font-medium">Assignee</th>
                    <th className="px-5 py-3 font-medium">Needs response</th>
                    <th className="px-5 py-3 font-medium">Response SLA</th>
                    <th className="px-5 py-3 font-medium">Resolution SLA</th>
                    <th className="px-5 py-3 font-medium">Age</th>
                    <th className="px-5 py-3 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {queue.map((item) => (
                    <tr key={item.ticket_id} className="hover:bg-slate-50/60">
                      <td className="px-5 py-4">
                        <a
                          href={`/tickets/${item.ticket_id}`}
                          className="font-medium text-slate-900 hover:text-violet-600"
                        >
                          {item.subject}
                        </a>
                        <div className="mt-1 text-xs text-slate-400">
                          #{item.ticket_id} · {item.status}
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <Badge variant={priorityVariant(item.priority)}>
                          {item.priority}
                        </Badge>
                      </td>
                      <td className="px-5 py-4">
                        {canWrite ? (
                          <select
                            value={item.service_queue_id ?? ""}
                            onChange={(e) =>
                              assignQueue(
                                item.ticket_id,
                                e.target.value ? Number(e.target.value) : null
                              )
                            }
                            className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs"
                          >
                            <option value="">No queue</option>
                            {queues.map((q) => (
                              <option key={q.id} value={q.id}>
                                {q.name}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span className="text-slate-600">
                            {item.service_queue_name ?? "—"}
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-4">
                        {item.assigned_subject ? (
                          <span className="text-slate-600">
                            {item.is_assigned_to_me ? "Me" : item.assigned_subject}
                          </span>
                        ) : canWrite ? (
                          <button
                            onClick={() => claimTicket(item.ticket_id)}
                            className="rounded-lg bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-600 hover:bg-violet-100"
                          >
                            Claim
                          </button>
                        ) : (
                          <span className="text-slate-400">Unassigned</span>
                        )}
                      </td>
                      <td className="px-5 py-4">
                        {item.needs_response ? (
                          <Badge variant="warning">Yes</Badge>
                        ) : (
                          <Badge variant="success">No</Badge>
                        )}
                      </td>
                      <td className="px-5 py-4">
                        {slaBadge(item.first_response_sla_state)}
                      </td>
                      <td className="px-5 py-4">
                        {slaBadge(item.resolution_sla_state)}
                      </td>
                      <td className="px-5 py-4 text-slate-500">
                        {formatAge(item.created_at)}
                      </td>
                      <td className="px-5 py-4">
                        {item.ai_routing_suggestion && canWrite && (
                          <button
                            onClick={() =>
                              applySuggestion(
                                item.ticket_id,
                                item.ai_routing_suggestion!.queue_id
                              )
                            }
                            className="rounded-lg bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-600 hover:bg-blue-100"
                            title={`AI suggests ${item.ai_routing_suggestion.queue_name}`}
                          >
                            Apply {item.ai_routing_suggestion.queue_name}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <p className="mt-4 text-xs text-slate-400">
            SLA times are elapsed wall-clock time (UTC). Business hours are not yet
            applied.
          </p>
        </div>
      </div>

      {showConfig && canManage && (
        <OperationsConfigModal
          queues={queues}
          slaPolicies={slaPolicies}
          activeTab={activeConfigTab}
          onTabChange={setActiveConfigTab}
          onClose={() => setShowConfig(false)}
          onSaved={() => {
            void loadConfig();
            void loadSummary();
            void loadQueue();
          }}
        />
      )}
    </main>
  );
}

function OperationsConfigModal({
  queues,
  slaPolicies,
  activeTab,
  onTabChange,
  onClose,
  onSaved,
}: {
  queues: ServiceQueue[];
  slaPolicies: SLAPolicy[];
  activeTab: "queues" | "sla";
  onTabChange: (tab: "queues" | "sla") => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-hidden rounded-[24px] bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
          <h2 className="text-lg font-semibold text-slate-950">
            Operations Setup
          </h2>
          <button
            onClick={onClose}
            className="rounded-lg p-2 text-slate-400 hover:bg-slate-50 hover:text-slate-600"
          >
            ✕
          </button>
        </div>

        <div className="flex border-b border-slate-100">
          <button
            onClick={() => onTabChange("queues")}
            className={`px-6 py-3 text-sm font-medium ${
              activeTab === "queues"
                ? "border-b-2 border-violet-500 text-violet-600"
                : "text-slate-500"
            }`}
          >
            Service Queues
          </button>
          <button
            onClick={() => onTabChange("sla")}
            className={`px-6 py-3 text-sm font-medium ${
              activeTab === "sla"
                ? "border-b-2 border-violet-500 text-violet-600"
                : "text-slate-500"
            }`}
          >
            SLA Policies
          </button>
        </div>

        <div className="overflow-y-auto p-6">
          {activeTab === "queues" ? (
            <QueueConfig queues={queues} slaPolicies={slaPolicies} onSaved={onSaved} />
          ) : (
            <SLAConfig slaPolicies={slaPolicies} onSaved={onSaved} />
          )}
        </div>
      </div>
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
