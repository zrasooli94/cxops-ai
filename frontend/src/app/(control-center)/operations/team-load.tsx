"use client";

import { LoaderCircle, RefreshCw, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "./badge.tsx";
import { type WorkloadMember } from "@/lib/operations/escalations.ts";
import { WORKLOAD_ENDPOINT } from "@/lib/operations/team-load";

export default function TeamLoad({
  onError,
}: {
  onError: (message: string) => void;
}) {
  const [members, setMembers] = useState<WorkloadMember[]>([]);
  const [loading, setLoading] = useState(false);

  const loadWorkload = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(WORKLOAD_ENDPOINT, { cache: "no-store" });
      if (!response.ok) throw new Error("Failed to load team workload");
      setMembers(await response.json());
    } catch (err) {
      onError(err instanceof Error ? err.message : "Workload error");
    } finally {
      setLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    void loadWorkload();
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [loadWorkload]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
            <Users className="h-5 w-5" />
          </div>
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
              Team members
            </p>
            <p className="text-2xl font-medium tracking-[-0.045em] text-slate-950">
              {members.length}
            </p>
          </div>
        </div>

        <button
          onClick={() => void loadWorkload()}
          disabled={loading}
          className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
          aria-label="Refresh workload"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="app-panel overflow-hidden rounded-[20px]">
        {loading && members.length === 0 ? (
          <div className="flex h-64 items-center justify-center">
            <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
          </div>
        ) : members.length === 0 ? (
          <div className="flex h-64 items-center justify-center text-sm text-slate-500">
            No workload data available.
          </div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-5 py-3 font-medium">Subject</th>
                <th className="px-5 py-3 font-medium">Open assigned</th>
                <th className="px-5 py-3 font-medium">Needs response</th>
                <th className="px-5 py-3 font-medium">Due soon</th>
                <th className="px-5 py-3 font-medium">Breached</th>
                <th className="px-5 py-3 font-medium">Urgent</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {members.map((member) => (
                <tr key={member.subject} className="hover:bg-slate-50/60">
                  <td className="px-5 py-4 font-medium text-slate-900">
                    {member.subject}
                  </td>
                  <td className="px-5 py-4">
                    <Badge variant="default">{member.open_assigned}</Badge>
                  </td>
                  <td className="px-5 py-4">
                    {member.needs_response > 0 ? (
                      <Badge variant="warning">{member.needs_response}</Badge>
                    ) : (
                      <Badge variant="success">0</Badge>
                    )}
                  </td>
                  <td className="px-5 py-4">
                    {member.due_soon > 0 ? (
                      <Badge variant="warning">{member.due_soon}</Badge>
                    ) : (
                      <Badge variant="success">0</Badge>
                    )}
                  </td>
                  <td className="px-5 py-4">
                    {member.breached > 0 ? (
                      <Badge variant="danger">{member.breached}</Badge>
                    ) : (
                      <Badge variant="success">0</Badge>
                    )}
                  </td>
                  <td className="px-5 py-4">
                    {member.urgent > 0 ? (
                      <Badge variant="danger">{member.urgent}</Badge>
                    ) : (
                      <Badge variant="success">0</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
