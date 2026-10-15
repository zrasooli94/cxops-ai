"use client";

import { AlertTriangle, ArrowUpRight, RefreshCw, Users } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { toHandoffOverview, type HandoffOverview } from "@/lib/dashboard/handoff-overview";
import { fetchPublicChatSummary } from "@/lib/public-chat/staff";

export default function StaffHandoffOverview() {
  const [snapshot, setSnapshot] = useState<HandoffOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const summary = await fetchPublicChatSummary("24h", (_url, _options) =>
        fetch(_url, { ..._options, signal }));
      if (!signal?.aborted) setSnapshot(toHandoffOverview(summary));
    } catch {
      if (!signal?.aborted) {
        setSnapshot(null);
        setError("Handoff status is unavailable. Try refreshing.");
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <section aria-label="Live handoff queue" className="mb-10 rounded-2xl border border-slate-200 bg-white p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-950">Human handoff queue</h2>
          <p className="mt-1 text-sm text-slate-500">Current tenant only · authenticated 24-hour operations summary</p>
        </div>
        <div className="flex items-center gap-4">
          <button type="button" aria-label="Refresh handoff queue"
            disabled={loading} onClick={() => void refresh()}
            className="rounded-lg border border-slate-200 p-2 text-slate-600 disabled:opacity-50">
            <RefreshCw className="h-4 w-4" />
          </button>
          <Link href="/public-chat" className="inline-flex items-center gap-1 text-sm font-semibold text-violet-700">
            Open handoffs <ArrowUpRight className="h-4 w-4" />
          </Link>
        </div>
      </div>
      {error ? (
        <p role="alert" className="mt-4 text-sm text-red-700">{error}</p>
      ) : loading && !snapshot ? (
        <p role="status" className="mt-5 text-sm text-slate-500">Loading handoff counts…</p>
      ) : snapshot ? (
        <div className="mt-5 grid gap-4 sm:grid-cols-3">
          <div className="rounded-xl bg-amber-50 p-4">
            <p className="text-sm text-amber-900">Waiting for staff</p>
            <p className="mt-2 text-3xl font-semibold text-amber-950">{snapshot.waiting}</p>
          </div>
          <div className="rounded-xl bg-violet-50 p-4">
            <p className="flex items-center gap-2 text-sm text-violet-900"><Users className="h-4 w-4" /> Assigned</p>
            <p className="mt-2 text-3xl font-semibold text-violet-950">{snapshot.assigned}</p>
          </div>
          <div className="rounded-xl bg-slate-50 p-4">
            <p className="text-sm text-slate-700">Oldest wait</p>
            <p className="mt-2 text-3xl font-semibold text-slate-950">{snapshot.oldestWaitingMinutes === null ? "—" : `${snapshot.oldestWaitingMinutes}m`}</p>
          </div>
          {snapshot.needsAttention && (
            <p className="flex items-center gap-2 text-sm font-medium text-amber-700 sm:col-span-3">
              <AlertTriangle className="h-4 w-4" /> Queue requires attention
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}
