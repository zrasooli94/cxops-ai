"use client";

import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  User,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  assignHandoffSession,
  fetchHandoffBusinessActions,
  fetchHandoffSessions,
  releaseHandoffSession,
} from "@/lib/public-chat/staff";
import type {
  StaffBusinessAction,
  StaffHandoffSession,
} from "@/lib/public-chat/types";
import { CAPABILITIES } from "@/lib/authorization/capabilities";
import { useAuthorization } from "@/lib/authorization/context";

function statusBadge(status: string) {
  switch (status) {
    case "human_assigned":
      return (
        <span className="inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700">
          Assigned
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
          Awaiting staff
        </span>
      );
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

function actionStatusBadge(status: string) {
  switch (status) {
    case "approved":
    case "executed":
    case "completed":
      return (
        <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
          {status}
        </span>
      );
    case "rejected":
    case "cancelled":
    case "failed":
      return (
        <span className="inline-flex items-center rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700">
          {status}
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-600">
          {status}
        </span>
      );
  }
}

export default function PublicChatPage() {
  const { can } = useAuthorization();
  const canRead = can(CAPABILITIES.TICKET_READ);
  const canWrite = can(CAPABILITIES.TICKET_WRITE);

  const [sessions, setSessions] = useState<StaffHandoffSession[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [actions, setActions] = useState<Record<number, StaffBusinessAction[]>>({});
  const [actionsLoading, setActionsLoading] = useState<Record<number, boolean>>({});
  const [busy, setBusy] = useState<number | null>(null);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchHandoffSessions();
      setSessions(result.sessions);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sessions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!canRead) return;
    /* eslint-disable react-hooks/set-state-in-effect */
    void loadSessions();
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [canRead, loadSessions]);

  const toggleActions = async (sessionId: number) => {
    const next = !expanded[sessionId];
    setExpanded((prev) => ({ ...prev, [sessionId]: next }));
    if (next && !actions[sessionId]) {
      setActionsLoading((prev) => ({ ...prev, [sessionId]: true }));
      try {
        const result = await fetchHandoffBusinessActions(sessionId);
        setActions((prev) => ({ ...prev, [sessionId]: result.actions }));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load actions");
      } finally {
        setActionsLoading((prev) => ({ ...prev, [sessionId]: false }));
      }
    }
  };

  const runAssignment = async (
    sessionId: number,
    action: "assign" | "release"
  ) => {
    setBusy(sessionId);
    try {
      if (action === "assign") {
        await assignHandoffSession(sessionId);
      } else {
        await releaseHandoffSession(sessionId);
      }
      await loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Assignment failed");
    } finally {
      setBusy(null);
    }
  };

  const awaiting = sessions.filter((s) => s.status === "human_requested").length;
  const assigned = sessions.filter((s) => s.status === "human_assigned").length;

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
                Public Web Chat
              </p>
              <h1 className="mt-2 text-3xl font-light tracking-[-0.04em] text-slate-950">
                Handoff Queue
              </h1>
            </div>
            <button
              onClick={() => void loadSessions()}
              disabled={loading}
              className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
              aria-label="Refresh"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
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

          {!canRead ? (
            <div className="app-panel flex h-64 flex-col items-center justify-center text-slate-500">
              <AlertTriangle className="mb-3 h-10 w-10 text-slate-300" />
              <p>You do not have permission to view the handoff queue.</p>
            </div>
          ) : (
            <>
              <div className="mb-6 grid gap-4 sm:grid-cols-3">
                <div className="app-panel rounded-[20px] p-5">
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                      <MessageSquareText className="h-5 w-5" />
                    </div>
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                        Awaiting staff
                      </p>
                      <p className="editorial-number text-3xl font-medium tracking-[-0.045em] text-slate-950">
                        {awaiting}
                      </p>
                    </div>
                  </div>
                </div>
                <div className="app-panel rounded-[20px] p-5">
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                      <User className="h-5 w-5" />
                    </div>
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                        Assigned
                      </p>
                      <p className="editorial-number text-3xl font-medium tracking-[-0.045em] text-slate-950">
                        {assigned}
                      </p>
                    </div>
                  </div>
                </div>
                <div className="app-panel rounded-[20px] p-5">
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
                      <CheckCircle2 className="h-5 w-5" />
                    </div>
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                        Total
                      </p>
                      <p className="editorial-number text-3xl font-medium tracking-[-0.045em] text-slate-950">
                        {awaiting + assigned}
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <div className="app-panel overflow-hidden rounded-[20px]">
                {loading && sessions.length === 0 ? (
                  <div className="flex h-64 items-center justify-center">
                    <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
                  </div>
                ) : sessions.length === 0 ? (
                  <div className="flex h-64 flex-col items-center justify-center text-slate-500">
                    <CheckCircle2 className="mb-3 h-10 w-10 text-slate-300" />
                    <p>No sessions are currently awaiting handoff.</p>
                  </div>
                ) : (
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="px-5 py-3 font-medium">Session</th>
                        <th className="px-5 py-3 font-medium">Status</th>
                        <th className="px-5 py-3 font-medium">Assignee</th>
                        <th className="px-5 py-3 font-medium">Created</th>
                        <th className="px-5 py-3 font-medium">Business actions</th>
                        <th className="px-5 py-3 font-medium">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {sessions.map((session) => (
                        <Pair
                          key={session.session_id}
                          session={session}
                          expanded={Boolean(expanded[session.session_id])}
                          actions={actions[session.session_id]}
                          actionsLoading={Boolean(
                            actionsLoading[session.session_id]
                          )}
                          busy={busy === session.session_id}
                          canWrite={canWrite}
                          onToggleActions={() => void toggleActions(session.session_id)}
                          onAssign={() => void runAssignment(session.session_id, "assign")}
                          onRelease={() => void runAssignment(session.session_id, "release")}
                        />
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              <p className="mt-4 text-xs text-slate-400">
                Sessions appear here when a customer requests a human handoff
                from the chat widget. Assigning takes the session and its
                business actions into your queue.
              </p>
            </>
          )}
        </div>
      </div>
    </main>
  );
}

function Pair({
  session,
  expanded,
  actions,
  actionsLoading,
  busy,
  canWrite,
  onToggleActions,
  onAssign,
  onRelease,
}: {
  session: StaffHandoffSession;
  expanded: boolean;
  actions: StaffBusinessAction[] | undefined;
  actionsLoading: boolean;
  busy: boolean;
  canWrite: boolean;
  onToggleActions: () => void;
  onAssign: () => void;
  onRelease: () => void;
}) {
  const isAssigned = session.status === "human_assigned";

  return (
    <>
      <tr className="hover:bg-slate-50/60">
        <td className="px-5 py-4">
          <a
            href={`/tickets/${session.ticket_id}`}
            className="font-medium text-slate-900 hover:text-violet-600"
          >
            Ticket #{session.ticket_id}
          </a>
          <div className="mt-1 text-xs text-slate-400">
            Session #{session.session_id} · Expires {formatAge(session.expires_at)}
          </div>
        </td>
        <td className="px-5 py-4">{statusBadge(session.status)}</td>
        <td className="px-5 py-4 text-slate-600">
          {session.assigned_to_subject ? (
            session.assigned_to_subject
          ) : (
            <span className="text-slate-400">Unassigned</span>
          )}
        </td>
        <td className="px-5 py-4 text-slate-500">
          {formatAge(session.created_at)}
        </td>
        <td className="px-5 py-4">
          <button
            onClick={onToggleActions}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:border-violet-300 hover:text-violet-600"
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            {expanded ? "Hide" : "View"}
          </button>
        </td>
        <td className="px-5 py-4">
          {canWrite ? (
            isAssigned ? (
              <button
                onClick={onRelease}
                disabled={busy}
                className="rounded-lg bg-white px-2.5 py-1 text-xs font-medium text-slate-600 border border-slate-200 hover:border-blue-300 hover:text-blue-600 disabled:opacity-50"
              >
                {busy ? "Working…" : "Release"}
              </button>
            ) : (
              <button
                onClick={onAssign}
                disabled={busy}
                className="rounded-lg bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-600 hover:bg-violet-100 disabled:opacity-50"
              >
                {busy ? "Working…" : "Assign to me"}
              </button>
            )
          ) : (
            <span className="text-xs text-slate-400">Read only</span>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-50/50">
          <td colSpan={6} className="px-5 py-4">
            <div className="rounded-[14px] border border-slate-200 bg-white p-4">
              <p className="mb-3 text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
                Business actions executed for this session
              </p>
              {actionsLoading ? (
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <LoaderCircle className="h-4 w-4 animate-spin text-violet-500" />
                  Loading actions…
                </div>
              ) : actions && actions.length > 0 ? (
                <ul className="divide-y divide-slate-100">
                  {actions.map((action) => (
                    <li
                      key={action.id}
                      className="flex items-center justify-between gap-4 py-2.5 text-sm"
                    >
                      <div>
                        <p className="font-medium text-slate-800">
                          {action.request_type}
                        </p>
                        <p className="mt-0.5 text-xs text-slate-400">
                          {action.summary ?? "—"}
                          {action.reference_id ? ` · ${action.reference_id}` : ""}
                        </p>
                      </div>
                      <div className="shrink-0">
                        {actionStatusBadge(action.status)}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-slate-500">
                  No business actions recorded for this session.
                </p>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}