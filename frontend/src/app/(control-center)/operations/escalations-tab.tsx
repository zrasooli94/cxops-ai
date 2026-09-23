"use client";

import {
  CheckCircle2,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "./badge.tsx";
import {
  buildEscalationSummaryCards,
  escalationStageVariant,
  formatDateTime,
  type EscalationListItem,
  type EscalationSummary,
} from "@/lib/operations/escalations.ts";

export default function EscalationsTab({
  canWrite,
  onError,
}: {
  canWrite: boolean;
  onError: (message: string) => void;
}) {
  const [summary, setSummary] = useState<EscalationSummary | null>(null);
  const [escalations, setEscalations] = useState<EscalationListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [acknowledging, setAcknowledging] = useState<number | null>(null);

  const [filters, setFilters] = useState({
    status: "",
    stage: "",
    milestone: "",
  });
  const [offset, setOffset] = useState(0);
  const limit = 25;

  const loadSummary = useCallback(async () => {
    try {
      const response = await fetch(
        "/api/backend/service-operations/escalations/summary",
        { cache: "no-store" }
      );
      if (!response.ok) throw new Error("Failed to load escalation summary");
      setSummary(await response.json());
    } catch (err) {
      onError(err instanceof Error ? err.message : "Escalation summary error");
    }
  }, [onError]);

  const loadEscalations = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(limit));
      params.set("offset", String(offset));
      if (filters.status) params.set("status", filters.status);
      if (filters.stage) params.set("stage", filters.stage);
      if (filters.milestone) params.set("milestone", filters.milestone);

      const response = await fetch(
        `/api/backend/service-operations/escalations?${params.toString()}`,
        { cache: "no-store" }
      );
      if (!response.ok) throw new Error("Failed to load escalations");
      setEscalations(await response.json());
    } catch (err) {
      onError(err instanceof Error ? err.message : "Escalations error");
    } finally {
      setLoading(false);
    }
  }, [filters, offset, onError]);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    void loadSummary();
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [loadSummary]);

  useEffect(() => {
    const timer = setTimeout(() => void loadEscalations(), 150);
    return () => clearTimeout(timer);
  }, [loadEscalations]);

  const acknowledge = async (id: number) => {
    setAcknowledging(id);
    try {
      const response = await fetch(
        `/api/backend/service-operations/escalations/${id}/acknowledge`,
        { method: "POST" }
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Acknowledge failed");
      }
      void loadSummary();
      void loadEscalations();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Acknowledge failed");
    } finally {
      setAcknowledging(null);
    }
  };

  const summaryCards = useMemo(
    () => (summary ? buildEscalationSummaryCards(summary) : []),
    [summary]
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {summaryCards.map((card) => (
            <div key={card.label} className="app-panel rounded-[20px] p-5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                {card.label}
              </p>
              <p className="editorial-number text-3xl font-medium tracking-[-0.045em] text-slate-950">
                {card.value}
              </p>
            </div>
          ))}
        </div>

        <button
          onClick={() => {
            void loadSummary();
            void loadEscalations();
          }}
          disabled={loading}
          className="ml-4 flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
          aria-label="Refresh escalations"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={filters.status}
          onChange={(e) => {
            setFilters((f) => ({ ...f, status: e.target.value }));
            setOffset(0);
          }}
          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
        >
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="resolved">Resolved</option>
        </select>

        <select
          value={filters.stage}
          onChange={(e) => {
            setFilters((f) => ({ ...f, stage: e.target.value }));
            setOffset(0);
          }}
          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
        >
          <option value="">All stages</option>
          <option value="breached">Breached</option>
          <option value="due_soon">Due soon</option>
          <option value="open">Open</option>
          <option value="acknowledged">Acknowledged</option>
          <option value="resolved">Resolved</option>
        </select>

        <select
          value={filters.milestone}
          onChange={(e) => {
            setFilters((f) => ({ ...f, milestone: e.target.value }));
            setOffset(0);
          }}
          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
        >
          <option value="">All milestones</option>
          <option value="first_response">First response</option>
          <option value="resolution">Resolution</option>
        </select>
      </div>

      <div className="app-panel overflow-hidden rounded-[20px]">
        {loading && escalations.length === 0 ? (
          <div className="flex h-64 items-center justify-center">
            <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
          </div>
        ) : escalations.length === 0 ? (
          <div className="flex h-64 flex-col items-center justify-center text-slate-500">
            <CheckCircle2 className="mb-3 h-10 w-10 text-slate-300" />
            <p>No escalations match the current filters.</p>
          </div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-5 py-3 font-medium">Escalation</th>
                <th className="px-5 py-3 font-medium">Priority</th>
                <th className="px-5 py-3 font-medium">Milestone</th>
                <th className="px-5 py-3 font-medium">Stage</th>
                <th className="px-5 py-3 font-medium">Due</th>
                <th className="px-5 py-3 font-medium">Triggered</th>
                <th className="px-5 py-3 font-medium">Assignee</th>
                <th className="px-5 py-3 font-medium">Queue</th>
                <th className="px-5 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {escalations.map((item) => (
                <tr key={item.id} className="hover:bg-slate-50/60">
                  <td className="px-5 py-4">
                    <a
                      href={`/tickets/${item.ticket_id}`}
                      className="font-medium text-slate-900 hover:text-violet-600"
                    >
                      {item.subject}
                    </a>
                    <div className="mt-1 text-xs text-slate-400">
                      #{item.ticket_id}
                    </div>
                  </td>
                  <td className="px-5 py-4">
                    <Badge
                      variant={
                        ["urgent", "high"].includes(item.priority.toLowerCase())
                          ? "warning"
                          : "default"
                      }
                    >
                      {item.priority}
                    </Badge>
                  </td>
                  <td className="px-5 py-4 capitalize text-slate-600">
                    {item.milestone.replace(/_/g, " ")}
                  </td>
                  <td className="px-5 py-4">
                    <Badge variant={escalationStageVariant(item.stage)}>
                      {item.stage}
                    </Badge>
                  </td>
                  <td className="px-5 py-4 text-slate-600">
                    {formatDateTime(item.due_at)}
                  </td>
                  <td className="px-5 py-4 text-slate-600">
                    {formatDateTime(item.triggered_at)}
                  </td>
                  <td className="px-5 py-4 text-slate-600">
                    {item.assigned_subject ?? "—"}
                  </td>
                  <td className="px-5 py-4 text-slate-600">
                    {item.service_queue_name ?? "—"}
                  </td>
                  <td className="px-5 py-4">
                    {item.stage !== "resolved" &&
                      item.stage !== "acknowledged" &&
                      canWrite && (
                        <button
                          onClick={() => acknowledge(item.id)}
                          disabled={acknowledging === item.id}
                          className="rounded-lg bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-600 hover:bg-violet-100 disabled:opacity-50"
                        >
                          {acknowledging === item.id
                            ? "Acknowledging..."
                            : "Acknowledge"}
                        </button>
                      )}
                    {item.acknowledged_at && (
                      <div className="text-xs text-slate-400">
                        Ack{" "}
                        {formatDateTime(item.acknowledged_at)}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="flex items-center justify-between">
        <button
          onClick={() => setOffset((o) => Math.max(0, o - limit))}
          disabled={offset === 0 || loading}
          className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
        >
          Previous
        </button>
        <span className="text-sm text-slate-500">
          Offset {offset}
        </span>
        <button
          onClick={() => setOffset((o) => o + limit)}
          disabled={escalations.length < limit || loading}
          className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
        >
          Next
        </button>
      </div>

      <p className="text-xs text-slate-400">
        Escalation stages are updated by the backend as SLA deadlines pass.
      </p>
    </div>
  );
}
