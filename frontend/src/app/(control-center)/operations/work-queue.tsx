"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  LoaderCircle,
  RefreshCw,
  Search,
  User,
  Users,
} from "lucide-react";
import { useMemo, type Dispatch, type SetStateAction } from "react";

import {
  Badge,
  formatAge,
  priorityVariant,
  slaBadge,
} from "./badge.tsx";
import type {
  OperationsQueueItem,
  OperationsSummary,
  ServiceQueue,
} from "./types.ts";

export type QueueFilters = {
  queue_id: string;
  status: string;
  priority: string;
  sla_state: string;
  needs_response: boolean;
  unassigned: boolean;
  mine: boolean;
  search: string;
};

export function useQueueSummaryCards(summary: OperationsSummary | null) {
  return useMemo(
    () => [
      { label: "Open", value: summary?.open ?? 0, icon: Users },
      { label: "Needs response", value: summary?.needs_response ?? 0, icon: AlertTriangle },
      { label: "Unassigned", value: summary?.unassigned ?? 0, icon: User },
      { label: "SLA breached", value: (summary?.response_breaches ?? 0) + (summary?.resolution_breaches ?? 0), icon: Clock },
      { label: "Due soon", value: summary?.due_soon ?? 0, icon: Clock },
    ],
    [summary]
  );
}

export default function WorkQueue({
  summary,
  queues,
  queue,
  loading,
  filters,
  setFilters,
  onRefresh,
  canWrite,
  onClaim,
  onApplySuggestion,
  onAssignQueue,
}: {
  summary: OperationsSummary | null;
  queues: ServiceQueue[];
  queue: OperationsQueueItem[];
  loading: boolean;
  filters: QueueFilters;
  setFilters: Dispatch<SetStateAction<QueueFilters>>;
  onRefresh: () => void;
  canWrite: boolean;
  onClaim: (ticketId: number) => void;
  onApplySuggestion: (ticketId: number, queueId: number) => void;
  onAssignQueue: (ticketId: number, queueId: number | null) => void;
}) {
  const summaryCards = useQueueSummaryCards(summary);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
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

        <button
          onClick={onRefresh}
          disabled={loading}
          className="ml-4 flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
          aria-label="Refresh"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
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
                          onAssignQueue(
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
                        onClick={() => onClaim(item.ticket_id)}
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
                          onApplySuggestion(
                            item.ticket_id,
                            item.ai_routing_suggestion!.queue_id
                          )
                        }
                        className="rounded-lg bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-600 hover:bg-blue-100"
                        title={`AI suggests ${item.ai_routing_suggestion!.queue_name}`}
                      >
                        Apply {item.ai_routing_suggestion!.queue_name}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p className="text-xs text-slate-400">
        SLA times are elapsed wall-clock time (UTC). Business hours are not yet
        applied.
      </p>
    </div>
  );
}
