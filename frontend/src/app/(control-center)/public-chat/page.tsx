"use client";

import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  User,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  SUMMARY_WINDOWS,
  assignHandoffSession,
  fetchHandoffBusinessActions,
  fetchHandoffSessions,
  fetchPublicChatSummary,
  releaseHandoffSession,
  resolveHandoffSession,
  type SummaryWindow,
} from "@/lib/public-chat/staff";
import type {
  PublicChatPilotSummary,
  StaffBusinessAction,
  StaffHandoffSession,
} from "@/lib/public-chat/types";
import { CAPABILITIES } from "@/lib/authorization/capabilities";
import { useAuthorization } from "@/lib/authorization/context";
import {
  applySummaryChangeFor,
  emptyPilotWorkbench,
  patchForOrganization,
  pilotWorkbenchForOrganization,
  visiblePilotWorkbench,
  type PilotWorkbenchState,
} from "@/lib/public-chat/pilotWorkbench";

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
  const { can, organizationId } = useAuthorization();
  const canRead = can(CAPABILITIES.TICKET_READ);
  const canWrite = can(CAPABILITIES.TICKET_WRITE);

  const [workbench, setWorkbench] = useState<PilotWorkbenchState>(() =>
    emptyPilotWorkbench(organizationId),
  );

  // Session awaiting the staff resolve confirmation. Cleared on tenant change
  // and on resolve completion/cancel so a dialog can never outlive its tenant.
  const [resolveTarget, setResolveTarget] =
    useState<StaffHandoffSession | null>(null);

  // Tenant-guarded state writes: every async operation captures the
  // organizationId when the request starts and routes every post-await
  // mutation through this setter, so a stale completion from a previous tenant
  // becomes a no-op instead of writing across the boundary.
  const setForOrganization = useCallback(
    (
      requestOrganizationId: number,
      updater: (prev: PilotWorkbenchState) => PilotWorkbenchState,
    ) => {
      setWorkbench(patchForOrganization(requestOrganizationId, updater));
    },
    [],
  );

  const [summaryWindow, setSummaryWindow] = useState<SummaryWindow>("24h");
  // Mirrors the active window so in-flight summary completions can compare
  // against it at resolve time (a 24h response must never replace a 7d one).
  const summaryWindowRef = useRef<SummaryWindow>("24h");

  // Render-time tenant gate. React effects run after render, so we do not rely
  // on the reset effect alone: the workbench displayed for the active
  // organization is derived here, guaranteeing that Org A's summary/sessions
  // cannot render under Org B even for a single frame. The empty copy is
  // transient and never written back — stored state changes only through the
  // guarded setters above.
  const visibleWorkbench = visiblePilotWorkbench(workbench, organizationId);

  const loadSessions = useCallback(async () => {
    const requestOrganizationId = organizationId;
    setForOrganization(requestOrganizationId, (prev) => ({
      ...prev,
      loading: true,
    }));
    try {
      const result = await fetchHandoffSessions();
      setForOrganization(requestOrganizationId, (prev) => ({
        ...prev,
        sessions: result.sessions,
      }));
    } catch (err) {
      setForOrganization(requestOrganizationId, (prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : "Failed to load sessions",
      }));
    } finally {
      setForOrganization(requestOrganizationId, (prev) => ({
        ...prev,
        loading: false,
      }));
    }
  }, [organizationId, setForOrganization]);

  const loadSummary = useCallback(async () => {
    const requestOrganizationId = organizationId;
    const requestWindow = summaryWindow;
    setForOrganization(requestOrganizationId, (prev) => ({
      ...prev,
      summaryLoading: true,
    }));
    try {
      const result = await fetchPublicChatSummary(requestWindow);
      setWorkbench(
        applySummaryChangeFor(
          requestOrganizationId,
          requestWindow,
          summaryWindowRef.current,
          (prev) => ({ ...prev, summary: result, summaryError: "" }),
        ),
      );
    } catch (err) {
      setWorkbench(
        applySummaryChangeFor(
          requestOrganizationId,
          requestWindow,
          summaryWindowRef.current,
          (prev) => ({
            ...prev,
            summaryError:
              err instanceof Error
                ? err.message
                : "Failed to load pilot summary",
          }),
        ),
      );
    } finally {
      setWorkbench(
        applySummaryChangeFor(
          requestOrganizationId,
          requestWindow,
          summaryWindowRef.current,
          (prev) => ({ ...prev, summaryLoading: false }),
        ),
      );
    }
  }, [summaryWindow, organizationId, setForOrganization]);

  // Tenant gate: the active organization is a hard boundary. When it changes,
  // the previous tenant's summary/queue/actions/errors are cleared BEFORE the
  // new tenant's data is requested, so org A's summary is never rendered as
  // "last good data" while org B is loading. Same-org renders are a no-op.
  useEffect(() => {
    setWorkbench((prev) =>
      pilotWorkbenchForOrganization(prev, organizationId),
    );
    setResolveTarget(null);
  }, [organizationId]);

  useEffect(() => {
    if (!canRead) return;
    void loadSessions();
    void loadSummary();
  }, [organizationId, canRead, loadSessions, loadSummary]);

  const selectSummaryWindow = (next: SummaryWindow) => {
    if (next === summaryWindow) return;
    // Update the ref synchronously so an in-flight summary completion for the
    // previous window is rejected as soon as the user switches (not after the
    // next effect runs post-render).
    summaryWindowRef.current = next;
    setForOrganization(organizationId, (prev) => ({
      ...prev,
      summary: null,
      summaryError: "",
    }));
    setSummaryWindow(next);
  };

  const toggleActions = async (sessionId: number) => {
    const requestOrganizationId = organizationId;
    const next = !visibleWorkbench.expanded[sessionId];
    setForOrganization(requestOrganizationId, (prev) => ({
      ...prev,
      expanded: { ...prev.expanded, [sessionId]: next },
    }));
    if (next && !visibleWorkbench.actions[sessionId]) {
      setForOrganization(requestOrganizationId, (prev) => ({
        ...prev,
        actionsLoading: { ...prev.actionsLoading, [sessionId]: true },
      }));
      try {
        const result = await fetchHandoffBusinessActions(sessionId);
        setForOrganization(requestOrganizationId, (prev) => ({
          ...prev,
          actions: { ...prev.actions, [sessionId]: result.actions },
        }));
      } catch (err) {
        setForOrganization(requestOrganizationId, (prev) => ({
          ...prev,
          error: err instanceof Error ? err.message : "Failed to load actions",
        }));
      } finally {
        setForOrganization(requestOrganizationId, (prev) => ({
          ...prev,
          actionsLoading: { ...prev.actionsLoading, [sessionId]: false },
        }));
      }
    }
  };

  const runAssignment = async (
    sessionId: number,
    action: "assign" | "release"
  ) => {
    const requestOrganizationId = organizationId;
    setForOrganization(requestOrganizationId, (prev) => ({
      ...prev,
      busy: sessionId,
    }));
    try {
      if (action === "assign") {
        await assignHandoffSession(sessionId);
      } else {
        await releaseHandoffSession(sessionId);
      }
      await loadSessions();
    } catch (err) {
      setForOrganization(requestOrganizationId, (prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : "Assignment failed",
      }));
    } finally {
      setForOrganization(requestOrganizationId, (prev) => ({
        ...prev,
        busy: null,
      }));
    }
  };

  const runResolve = async (session: StaffHandoffSession) => {
    const requestOrganizationId = organizationId;
    setResolveTarget(null);
    setForOrganization(requestOrganizationId, (prev) => ({
      ...prev,
      busy: session.session_id,
    }));
    try {
      await resolveHandoffSession(session.session_id);
      setForOrganization(requestOrganizationId, (prev) => ({
        ...prev,
        sessions: prev.sessions.filter(
          (s) => s.session_id !== session.session_id,
        ),
      }));
      void loadSummary();
    } catch (err) {
      // The resolved session stays visible so the operator sees exactly what
      // failed; a retry is possible from the same row.
      setForOrganization(requestOrganizationId, (prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : "Failed to resolve session",
      }));
    } finally {
      setForOrganization(requestOrganizationId, (prev) => ({
        ...prev,
        busy: null,
      }));
    }
  };

  const awaiting = visibleWorkbench.sessions.filter(
    (s) => s.status === "human_requested",
  ).length;
  const assigned = visibleWorkbench.sessions.filter(
    (s) => s.status === "human_assigned",
  ).length;

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
              disabled={visibleWorkbench.loading}
              className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
              aria-label="Refresh"
            >
              <RefreshCw
                className={`h-4 w-4 ${visibleWorkbench.loading ? "animate-spin" : ""}`}
              />
            </button>
          </div>
        </header>

        <div className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          {visibleWorkbench.error && (
            <div className="mb-5 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
              {visibleWorkbench.error}
              <button
                onClick={() =>
                  setForOrganization(organizationId, (prev) => ({
                    ...prev,
                    error: "",
                  }))
                }
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
              <PilotSummaryPanel
                summary={visibleWorkbench.summary}
                loading={visibleWorkbench.summaryLoading}
                error={visibleWorkbench.summaryError}
                window={summaryWindow}
                onSelectWindow={selectSummaryWindow}
                onRefresh={() => void loadSummary()}
              />

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
                {visibleWorkbench.loading && visibleWorkbench.sessions.length === 0 ? (
                  <div className="flex h-64 items-center justify-center">
                    <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
                  </div>
                ) : visibleWorkbench.sessions.length === 0 ? (
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
                      {visibleWorkbench.sessions.map((session) => (
                        <Pair
                          key={session.session_id}
                          session={session}
                          expanded={Boolean(visibleWorkbench.expanded[session.session_id])}
                          actions={visibleWorkbench.actions[session.session_id]}
                          actionsLoading={Boolean(
                            visibleWorkbench.actionsLoading[session.session_id]
                          )}
                          busy={visibleWorkbench.busy === session.session_id}
                          canWrite={canWrite}
                          onToggleActions={() => void toggleActions(session.session_id)}
                          onAssign={() => void runAssignment(session.session_id, "assign")}
                          onRelease={() => void runAssignment(session.session_id, "release")}
                          onResolve={() => setResolveTarget(session)}
                        />
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              <p className="mt-4 text-xs text-slate-400">
                Sessions appear here when a customer requests a human handoff
                from the chat widget. Assigning takes the session and its
                business actions into your queue; resolving closes the chat and
                marks the linked ticket solved.
              </p>

              {resolveTarget && (
                <div
                  className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
                  role="dialog"
                  aria-modal="true"
                  aria-labelledby="resolve-dialog-title"
                >
                  <div className="w-full max-w-md rounded-[20px] border border-slate-200 bg-white p-6 shadow-xl">
                    <h2
                      id="resolve-dialog-title"
                      className="text-lg font-medium tracking-[-0.02em] text-slate-950"
                    >
                      Resolve this conversation?
                    </h2>
                    <p className="mt-2 text-sm text-slate-600">
                      The chat will be closed and the linked ticket marked
                      solved.
                    </p>
                    <p className="mt-1 text-sm text-slate-600">
                      Messages and history will be preserved.
                    </p>
                    <div className="mt-6 flex justify-end gap-3">
                      <button
                        onClick={() => setResolveTarget(null)}
                        disabled={
                          visibleWorkbench.busy === resolveTarget.session_id
                        }
                        className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => void runResolve(resolveTarget)}
                        disabled={
                          visibleWorkbench.busy === resolveTarget.session_id
                        }
                        className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                      >
                        {visibleWorkbench.busy === resolveTarget.session_id
                          ? "Working…"
                          : "Resolve"}
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </main>
  );
}

function formatCost(value: number) {
  if (value <= 0) return "$0";
  return `$${value.toFixed(3)}`;
}

function formatCount(value: number) {
  return value.toLocaleString("en-US");
}

function pilotBadge(
  active: boolean,
  label: string,
  onLabel: string,
  offLabel: string,
) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ${
        active
          ? "border border-emerald-200 bg-emerald-50 text-emerald-700"
          : "border border-amber-200 bg-amber-50 text-amber-700"
      }`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          active ? "bg-emerald-500" : "bg-amber-500"
        }`}
      />
      {label}: {active ? onLabel : offLabel}
    </span>
  );
}

function summaryTile(
  icon: ReactNode,
  label: string,
  value: string,
  tone: "neutral" | "warn" = "neutral",
) {
  return (
    <div className="rounded-[16px] border border-slate-200 bg-white p-4">
      <div className="flex items-center gap-2.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
          {icon}
        </div>
        <div className="min-w-0">
          <p className="truncate text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
            {label}
          </p>
          <p
            className={`editorial-number text-2xl font-medium tracking-[-0.045em] ${
              tone === "warn" ? "text-red-600" : "text-slate-950"
            }`}
          >
            {value}
          </p>
        </div>
      </div>
    </div>
  );
}

function PilotSummaryPanel({
  summary,
  loading,
  error,
  window,
  onSelectWindow,
  onRefresh,
}: {
  summary: PublicChatPilotSummary | null;
  loading: boolean;
  error: string;
  window: SummaryWindow;
  onSelectWindow: (window: SummaryWindow) => void;
  onRefresh: () => void;
}) {
  const empty = summary === null;
  const safetyHit =
    (summary?.safety.public_chat_integration_jobs ?? 0) > 0 ||
    (summary?.safety.autonomous_public_chat_executions ?? 0) > 0;

  return (
    <div className="app-panel mb-6 rounded-[20px] p-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
            <Activity className="h-5 w-5" />
          </div>
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Live Pilot
            </p>
            <h2 className="text-xl font-medium tracking-[-0.03em] text-slate-950">
              Pilot Operations
            </h2>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-full border border-slate-200 bg-white p-0.5">
            {SUMMARY_WINDOWS.map((option) => (
              <button
                key={option}
                onClick={() => onSelectWindow(option)}
                className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                  option === window
                    ? "bg-violet-50 text-violet-700"
                    : "text-slate-500 hover:text-violet-600"
                }`}
              >
                {option === "24h" ? "24 hours" : "7 days"}
              </button>
            ))}
          </div>
          <button
            onClick={onRefresh}
            disabled={loading}
            aria-label="Refresh pilot summary"
            className="flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-4 flex items-start gap-3 rounded-[14px] border border-red-200 bg-red-50/80 p-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {error}
            {summary ? " Showing the last successful summary." : ""}
          </span>
        </div>
      )}

      {empty && loading ? (
        <div className="mt-4 flex h-40 items-center justify-center">
          <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
        </div>
      ) : empty ? (
        <div className="mt-4 flex h-40 flex-col items-center justify-center text-slate-500">
          <AlertTriangle className="mb-3 h-10 w-10 text-slate-300" />
          <p>Could not load the pilot summary.</p>
        </div>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {pilotBadge(
              summary.config.widget_enabled,
              "Widget",
              "Live",
              "Disabled",
            )}
            {pilotBadge(
              summary.config.grounded_auto_reply_enabled,
              "Grounded answers",
              "Enabled",
              "Disabled",
            )}
            {pilotBadge(
              summary.queue_health.health === "normal",
              "Queue",
              "Healthy",
              "Needs attention",
            )}
            <span className="inline-flex items-center rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-500">
              Tenant #{summary.tenant_id} · {window} window
            </span>
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {summaryTile(
              <MessageSquareText className="h-4 w-4" />,
              "Sessions",
              formatCount(summary.window_summary.sessions_created),
            )}
            {summaryTile(
              <User className="h-4 w-4" />,
              "Customer messages",
              formatCount(summary.window_summary.customer_messages),
            )}
            {summaryTile(
              <CheckCircle2 className="h-4 w-4" />,
              "Grounded AI answers",
              formatCount(summary.window_summary.grounded_public_auto_replies),
            )}
            {summaryTile(
              <Activity className="h-4 w-4" />,
              "Sessions closed",
              formatCount(summary.window_summary.sessions_closed),
            )}
            {summaryTile(
              <MessageSquareText className="h-4 w-4" />,
              "Awaiting staff",
              formatCount(summary.queue.human_requested),
            )}
            {summaryTile(
              <User className="h-4 w-4" />,
              "Assigned",
              formatCount(summary.queue.human_assigned),
            )}
            {summaryTile(
              <AlertTriangle className="h-4 w-4" />,
              "RAG errors",
              formatCount(summary.rag.rag_errors),
              summary.rag.rag_errors > 0 ? "warn" : "neutral",
            )}
            {summaryTile(
              <Activity className="h-4 w-4" />,
              "AI cost",
              formatCost(summary.rag.estimated_ai_cost_usd),
            )}
          </div>

          {safetyHit && (
            <div className="mt-4 flex items-start gap-3 rounded-[14px] border border-red-300 bg-red-50 p-3 text-sm text-red-800">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                Integration jobs or autonomous executions attributable to public
                chat were detected in this window. Review before proceeding.
              </span>
            </div>
          )}

          <p className="mt-4 text-xs text-slate-400">
            Read-only aggregates over durable rows; refreshing never writes.
            Queue counts and the waiting signal cover only live sessions
            (expired sessions are excluded), and the attention threshold is an
            operator signal, not an SLA. RAG rows recorded before Phase 1P.7
            carry the shared rag_answer label and are excluded; latency averages
            successful rows only, and AI cost is a lower bound because failed
            requests without token usage cannot be costed exactly.
          </p>
        </>
      )}
    </div>
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
  onResolve,
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
  onResolve: () => void;
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
            <div className="flex items-center gap-2">
              {isAssigned ? (
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
              )}
              <button
                onClick={onResolve}
                disabled={busy}
                className="rounded-lg bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
              >
                {busy ? "Working…" : "Resolve"}
              </button>
            </div>
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