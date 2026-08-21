"use client";

import {
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Clock3,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Workflow,
  X,
  XCircle,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import AppSidebar from "@/components/app-sidebar";

type ToolPlanItem = {
  tool: string;
  arguments: Record<string, unknown>;
  risk_level: "low" | "medium" | "high";
  requires_approval: boolean;
  authorized: boolean;
};

type AgentRun = {
  run_id: string;
  ticket_id: number;
  action: string;
  status: string;
  reason: string;
  recommended_team: string | null;
  recommended_priority: string | null;
  response_draft: string | null;
  requires_human_approval: boolean;
  reviewer_note: string | null;
  workflow_path: string[];
  tool_plan: ToolPlanItem[];
};

type TicketRecord = {
  id: number;
  subject: string;
  requester_email: string;
};

type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "violet";

function Badge({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?: BadgeVariant;
}) {
  const styles: Record<BadgeVariant, string> = {
    default:
      "border-slate-200 bg-slate-50 text-slate-600",
    success:
      "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning:
      "border-amber-200 bg-amber-50 text-amber-700",
    danger:
      "border-rose-200 bg-rose-50 text-rose-700",
    info:
      "border-blue-200 bg-blue-50 text-blue-700",
    violet:
      "border-violet-200 bg-violet-50 text-violet-700",
  };

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

function riskVariant(
  risk: string,
): BadgeVariant {
  if (risk === "high") {
    return "danger";
  }

  if (risk === "medium") {
    return "warning";
  }

  return "success";
}

function actionVariant(
  action: string,
): BadgeVariant {
  if (action === "escalate") {
    return "danger";
  }

  if (action === "human_review") {
    return "warning";
  }

  if (
    action === "respond" ||
    action === "route"
  ) {
    return "info";
  }

  if (action === "internal_note") {
    return "success";
  }

  return "default";
}

function formatText(value: string) {
  return value
    .split("_")
    .map(
      (part) =>
        part.charAt(0).toUpperCase() +
        part.slice(1),
    )
    .join(" ");
}

function StatCard({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: number;
  note: string;
  tone: "amber" | "rose" | "blue";
}) {
  const styles = {
    amber: {
      icon: "bg-amber-50 text-amber-500",
      glow: "bg-amber-300/10",
    },
    rose: {
      icon: "bg-rose-50 text-rose-500",
      glow: "bg-rose-300/10",
    },
    blue: {
      icon: "bg-blue-50 text-blue-500",
      glow: "bg-blue-300/10",
    },
  }[tone];

  return (
    <div className="app-panel relative overflow-hidden rounded-[20px] p-6">
      <div
        className={`absolute -right-8 -top-8 h-28 w-28 rounded-full blur-3xl ${styles.glow}`}
      />

      <div className="relative">
        <div
          className={`flex h-9 w-9 items-center justify-center rounded-xl ${styles.icon}`}
        >
          {tone === "amber" ? (
            <Clock3 className="h-4 w-4" />
          ) : tone === "rose" ? (
            <ShieldAlert className="h-4 w-4" />
          ) : (
            <Workflow className="h-4 w-4" />
          )}
        </div>

        <p className="mt-5 text-[10px] font-semibold uppercase tracking-[0.15em] text-slate-400">
          {label}
        </p>

        <p className="editorial-number mt-2 text-4xl font-medium tracking-[-0.045em] text-slate-950">
          {value}
        </p>

        <p className="mt-3 text-xs leading-5 text-slate-500">
          {note}
        </p>
      </div>
    </div>
  );
}

export default function ApprovalsPage() {
  const [runs, setRuns] =
    useState<AgentRun[]>([]);

  const [reviewRuns, setReviewRuns] =
    useState<AgentRun[]>([]);

  const [tickets, setTickets] =
    useState<TicketRecord[]>([]);

  const [selectedRun, setSelectedRun] =
    useState<AgentRun | null>(null);

  const [reviewNote, setReviewNote] =
    useState("");

  const [loading, setLoading] =
    useState(true);

  const [
    actionLoading,
    setActionLoading,
  ] = useState(false);

  const [error, setError] =
    useState("");

  const [success, setSuccess] =
    useState("");

  const loadData =
    useCallback(async () => {
      setLoading(true);
      setError("");

      try {
        const [
          pendingResponse,
          reviewResponse,
          ticketsResponse,
        ] = await Promise.all([
          fetch(
            "/api/backend/agent/runs?run_status=pending_approval&limit=100",
            {
              cache: "no-store",
            },
          ),
          fetch(
            "/api/backend/agent/runs?run_status=review_required&limit=100",
            {
              cache: "no-store",
            },
          ),
          fetch("/api/backend/tickets", {
            cache: "no-store",
          }),
        ]);

        if (
          !pendingResponse.ok ||
          !reviewResponse.ok ||
          !ticketsResponse.ok
        ) {
          throw new Error(
            "Failed to load approval queue.",
          );
        }

        const pendingData =
          (await pendingResponse.json()) as AgentRun[];

        const reviewData =
          (await reviewResponse.json()) as AgentRun[];

        const ticketData =
          await ticketsResponse.json();

        const ticketRows: TicketRecord[] =
          Array.isArray(ticketData)
            ? ticketData
            : Array.isArray(ticketData.items)
              ? ticketData.items
              : [];

        const actionable =
          pendingData.filter(
            (run) =>
              run.action !== "no_action",
          );

        setRuns(actionable);
        setReviewRuns(reviewData);
        setTickets(ticketRows);

        setSelectedRun((current) => {
          if (
            current &&
            actionable.some(
              (run) =>
                run.run_id ===
                current.run_id,
            )
          ) {
            return current;
          }

          return actionable[0] ?? null;
        });
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load queue.",
        );
      } finally {
        setLoading(false);
      }
    }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadData();
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [loadData]);

  const ticketMap = useMemo(() => {
    return new Map(
      tickets.map((ticket) => [
        ticket.id,
        ticket,
      ]),
    );
  }, [tickets]);

  const highRiskCount = useMemo(
    () =>
      runs.filter((run) =>
        run.tool_plan.some(
          (tool) =>
            tool.risk_level === "high",
        ),
      ).length,
    [runs],
  );

  async function approveAndQueue() {
    if (!selectedRun) {
      return;
    }

    setActionLoading(true);
    setError("");
    setSuccess("");

    try {
      const approveResponse =
        await fetch(
          `/api/backend/agent/runs/${selectedRun.run_id}/approve`,
          {
            method: "POST",
            headers: {
              "content-type":
                "application/json",
            },
            body: JSON.stringify({
              note:
                reviewNote.trim() ||
                "Approved from CXOps Control Center",
            }),
          },
        );

      const approved =
        await approveResponse.json();

      if (!approveResponse.ok) {
        throw new Error(
          approved?.detail ??
            "Approval failed.",
        );
      }

      if (
        selectedRun.action ===
        "human_review"
      ) {
        setSuccess(
          "Run moved to human review.",
        );

        setReviewNote("");
        await loadData();
        return;
      }

      const executeResponse =
        await fetch(
          `/api/backend/agent/runs/${selectedRun.run_id}/execute`,
          {
            method: "POST",
            cache: "no-store",
          },
        );

      const execution =
        await executeResponse.json();

      if (!executeResponse.ok) {
        throw new Error(
          execution?.detail ??
            "Run approved but queueing failed.",
        );
      }

      setSuccess(
        `Approved and queued successfully${
          execution.job_id
            ? ` — job #${execution.job_id}`
            : ""
        }.`,
      );

      setReviewNote("");
      await loadData();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Approval failed.",
      );
    } finally {
      setActionLoading(false);
    }
  }

  async function rejectRun() {
    if (!selectedRun) {
      return;
    }

    setActionLoading(true);
    setError("");
    setSuccess("");

    try {
      const response = await fetch(
        `/api/backend/agent/runs/${selectedRun.run_id}/reject`,
        {
          method: "POST",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify({
            note:
              reviewNote.trim() ||
              "Rejected from CXOps Control Center",
          }),
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        throw new Error(
          body?.detail ??
            "Rejection failed.",
        );
      }

      setSuccess(
        "Agent run rejected.",
      );

      setReviewNote("");
      await loadData();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Rejection failed.",
      );
    } finally {
      setActionLoading(false);
    }
  }

  const selectedTicket =
    selectedRun
      ? ticketMap.get(
          selectedRun.ticket_id,
        )
      : undefined;

  const approvalToolCount =
    selectedRun?.tool_plan.filter(
      (tool) =>
        tool.requires_approval,
    ).length ?? 0;

  const highestRisk =
    selectedRun?.tool_plan.some(
      (tool) =>
        tool.risk_level === "high",
    )
      ? "high"
      : selectedRun?.tool_plan.some(
            (tool) =>
              tool.risk_level ===
              "medium",
          )
        ? "medium"
        : "low";

  return (
    <div className="min-h-screen">
      <AppSidebar active="/approvals" />

      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Approval Queue
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Human-in-the-loop safety
              </p>
            </div>

            <div className="flex items-center gap-3">
              <div className="hidden items-center gap-2 rounded-full border border-slate-200 bg-white/80 px-4 py-2 text-[11px] text-slate-500 shadow-sm sm:flex">
                <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]" />
                Safety controls active
              </div>

              <button
                type="button"
                onClick={() =>
                  void loadData()
                }
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh approval queue"
              >
                <RefreshCw
                  className={`h-4 w-4 ${
                    loading
                      ? "animate-spin"
                      : ""
                  }`}
                />
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Human-in-the-loop
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Approval Queue
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Review sensitive AI actions
              before they can reach external
              systems. Tool risk, reasoning
              and execution intent remain
              visible to the reviewer.
            </p>
          </section>

          {error && (
            <div className="mb-5 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
              {error}
            </div>
          )}

          {success && (
            <div className="mb-5 flex items-start gap-3 rounded-[18px] border border-emerald-200 bg-emerald-50/80 p-4 text-sm text-emerald-700">
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" />
              {success}
            </div>
          )}

          <section className="mb-7 grid gap-4 md:grid-cols-3">
            <StatCard
              label="Awaiting approval"
              value={runs.length}
              note="Agent decisions currently waiting for an authorized reviewer."
              tone="amber"
            />

            <StatCard
              label="High-risk runs"
              value={highRiskCount}
              note="Pending decisions containing at least one high-risk tool."
              tone="rose"
            />

            <StatCard
              label="Human review cases"
              value={reviewRuns.length}
              note="Runs explicitly routed into human review rather than execution."
              tone="blue"
            />
          </section>

          <div className="grid gap-6 xl:grid-cols-[390px_minmax(0,1fr)]">
            <section className="app-panel self-start overflow-hidden rounded-[22px] xl:sticky xl:top-[96px]">
              <div className="flex items-center justify-between border-b border-slate-200/70 p-5">
                <div>
                  <h2 className="font-medium text-slate-900">
                    Pending Decisions
                  </h2>

                  <p className="mt-1 text-xs text-slate-400">
                    Actions awaiting review
                  </p>
                </div>

                <Badge variant="warning">
                  {runs.length}
                </Badge>
              </div>

              <div
                data-lenis-prevent
                className="max-h-[750px] overflow-y-auto overscroll-contain"
              >
                {loading ? (
                  <div className="flex h-48 items-center justify-center">
                    <LoaderCircle className="h-6 w-6 animate-spin text-violet-500" />
                  </div>
                ) : runs.length === 0 ? (
                  <div className="p-10 text-center">
                    <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-500">
                      <CheckCircle2 className="h-6 w-6" />
                    </div>

                    <p className="mt-4 font-medium text-slate-800">
                      Queue is clear
                    </p>

                    <p className="mt-1 text-xs text-slate-400">
                      No approval-controlled actions
                      are waiting.
                    </p>
                  </div>
                ) : (
                  runs.map((run) => {
                    const ticket =
                      ticketMap.get(
                        run.ticket_id,
                      );

                    const selected =
                      selectedRun?.run_id ===
                      run.run_id;

                    const runRisk =
                      run.tool_plan.some(
                        (tool) =>
                          tool.risk_level ===
                          "high",
                      )
                        ? "high"
                        : run.tool_plan.some(
                              (tool) =>
                                tool.risk_level ===
                                "medium",
                            )
                          ? "medium"
                          : "low";

                    return (
                      <button
                        key={run.run_id}
                        type="button"
                        onClick={() => {
                          setSelectedRun(run);
                          setReviewNote("");
                          setError("");
                          setSuccess("");
                        }}
                        className={`group relative w-full border-b border-slate-200/60 p-5 text-left transition last:border-b-0 ${
                          selected
                            ? "bg-gradient-to-r from-violet-50/90 via-blue-50/40 to-white"
                            : "bg-white/30 hover:bg-slate-50/80"
                        }`}
                      >
                        {selected && (
                          <span className="absolute bottom-3 left-0 top-3 w-[3px] rounded-r-full bg-gradient-to-b from-violet-500 to-blue-500" />
                        )}

                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-slate-400">
                              Ticket #
                              {run.ticket_id}
                            </p>

                            <p className="mt-2 truncate text-sm font-medium text-slate-850">
                              {ticket?.subject ??
                                formatText(
                                  run.action,
                                )}
                            </p>
                          </div>

                          <ChevronRight
                            className={`mt-1 h-4 w-4 shrink-0 transition ${
                              selected
                                ? "translate-x-0.5 text-violet-500"
                                : "text-slate-300 group-hover:translate-x-0.5 group-hover:text-slate-500"
                            }`}
                          />
                        </div>

                        <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">
                          {run.reason}
                        </p>

                        <div className="mt-3 flex flex-wrap gap-2">
                          <Badge
                            variant={actionVariant(
                              run.action,
                            )}
                          >
                            {formatText(
                              run.action,
                            )}
                          </Badge>

                          <Badge
                            variant={riskVariant(
                              runRisk,
                            )}
                          >
                            {runRisk} risk
                          </Badge>
                        </div>
                      </button>
                    );
                  })
                )}
              </div>
            </section>

            {!selectedRun ? (
              <div className="app-panel flex min-h-[560px] items-center justify-center rounded-[22px]">
                <div className="text-center">
                  <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-50 to-blue-50 text-violet-500">
                    <ShieldCheck className="h-6 w-6" />
                  </div>

                  <p className="mt-4 font-medium text-slate-800">
                    Select an agent run
                  </p>

                  <p className="mt-1 text-sm text-slate-400">
                    Review details will appear here.
                  </p>
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                <section className="app-panel overflow-hidden rounded-[22px]">
                  <div className="border-b border-slate-200/70 bg-gradient-to-r from-amber-50/75 via-white to-violet-50/60 p-6 md:p-7">
                    <div className="flex flex-col justify-between gap-5 md:flex-row md:items-start">
                      <div>
                        <div className="flex flex-wrap gap-2">
                          <Badge
                            variant={actionVariant(
                              selectedRun.action,
                            )}
                          >
                            {formatText(
                              selectedRun.action,
                            )}
                          </Badge>

                          <Badge
                            variant={riskVariant(
                              highestRisk,
                            )}
                          >
                            {highestRisk} risk
                          </Badge>

                          {approvalToolCount >
                            0 && (
                            <Badge variant="warning">
                              {approvalToolCount}{" "}
                              approval-controlled{" "}
                              {approvalToolCount ===
                              1
                                ? "tool"
                                : "tools"}
                            </Badge>
                          )}
                        </div>

                        <h2 className="mt-5 text-2xl font-medium tracking-[-0.035em] text-slate-950">
                          {selectedTicket?.subject ??
                            `Ticket #${selectedRun.ticket_id}`}
                        </h2>

                        <p className="mt-2 break-all font-mono text-[10px] text-slate-400">
                          {selectedRun.run_id}
                        </p>
                      </div>

                      <div className="flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-4 py-2 text-xs font-medium text-amber-700">
                        <ShieldAlert className="h-4 w-4" />
                        Awaiting reviewer
                      </div>
                    </div>
                  </div>

                  <div className="p-6 md:p-7">
                    {selectedTicket && (
                      <div className="mb-4 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                        <span>
                          Ticket #
                          {selectedRun.ticket_id}
                        </span>

                        <span>•</span>

                        <span>
                          {
                            selectedTicket.requester_email
                          }
                        </span>
                      </div>
                    )}

                    <div className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                        AI reasoning
                      </p>

                      <p className="mt-3 text-sm leading-7 text-slate-600">
                        {selectedRun.reason}
                      </p>
                    </div>

                    {selectedRun.response_draft && (
                      <div className="mt-4 rounded-2xl border border-blue-100 bg-blue-50/45 p-5">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-500">
                          Proposed customer reply
                        </p>

                        <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-600">
                          {
                            selectedRun.response_draft
                          }
                        </p>
                      </div>
                    )}
                  </div>
                </section>

                <section className="app-panel rounded-[22px] p-6 md:p-7">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                      <Workflow className="h-5 w-5" />
                    </div>

                    <div>
                      <h2 className="font-medium text-slate-900">
                        Proposed Tool Plan
                      </h2>

                      <p className="text-xs text-slate-400">
                        External operations requiring
                        reviewer awareness
                      </p>
                    </div>
                  </div>

                  <div className="mt-6 space-y-4">
                    {selectedRun.tool_plan.map(
                      (tool, index) => (
                        <div
                          key={`${tool.tool}-${index}`}
                          className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5"
                        >
                          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                            <div>
                              <p className="font-mono text-sm font-medium text-violet-600">
                                {tool.tool}
                              </p>

                              <p className="mt-1 text-[10px] uppercase tracking-[0.1em] text-slate-400">
                                Tool {index + 1}
                              </p>
                            </div>

                            <div className="flex flex-wrap gap-2">
                              <Badge
                                variant={riskVariant(
                                  tool.risk_level,
                                )}
                              >
                                {tool.risk_level} risk
                              </Badge>

                              <Badge
                                variant={
                                  tool.requires_approval
                                    ? "warning"
                                    : "success"
                                }
                              >
                                {tool.requires_approval
                                  ? "Approval required"
                                  : "Pre-authorized"}
                              </Badge>

                              <Badge
                                variant={
                                  tool.authorized
                                    ? "success"
                                    : "warning"
                                }
                              >
                                {tool.authorized
                                  ? "Authorized"
                                  : "Locked"}
                              </Badge>
                            </div>
                          </div>

                          {Object.keys(
                            tool.arguments ?? {},
                          ).length > 0 && (
                            <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
                              <div className="border-b border-slate-200 px-4 py-2.5">
                                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                                  Arguments
                                </p>
                              </div>

                              <pre className="overflow-x-auto p-4 text-xs leading-6 text-slate-600">
                                {JSON.stringify(
                                  tool.arguments,
                                  null,
                                  2,
                                )}
                              </pre>
                            </div>
                          )}
                        </div>
                      ),
                    )}
                  </div>
                </section>

                <section className="app-panel overflow-hidden rounded-[22px]">
                  <div className="border-b border-slate-200/70 p-6 md:p-7">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-50 text-amber-500">
                        <ShieldCheck className="h-5 w-5" />
                      </div>

                      <div>
                        <h2 className="font-medium text-slate-900">
                          Reviewer Decision
                        </h2>

                        <p className="text-xs text-slate-400">
                          Record the reasoning behind
                          this decision
                        </p>
                      </div>
                    </div>

                    <textarea
                      value={reviewNote}
                      onChange={(event) =>
                        setReviewNote(
                          event.target.value,
                        )
                      }
                      rows={4}
                      placeholder="Reviewer note..."
                      className="mt-6 w-full resize-none rounded-2xl border border-slate-200 bg-[#fbfcff] p-4 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                    />

                    <div className="mt-5 flex flex-col gap-3 sm:flex-row">
                      <button
                        type="button"
                        onClick={() =>
                          void approveAndQueue()
                        }
                        disabled={actionLoading}
                        className="flex flex-1 items-center justify-center gap-2 rounded-full bg-gradient-to-r from-emerald-500 to-teal-500 px-5 py-3.5 text-sm font-medium text-white shadow-[0_10px_25px_rgba(16,185,129,0.18)] transition hover:-translate-y-0.5 disabled:opacity-50"
                      >
                        {actionLoading ? (
                          <LoaderCircle className="h-4 w-4 animate-spin" />
                        ) : selectedRun.action ===
                          "human_review" ? (
                          <ShieldCheck className="h-4 w-4" />
                        ) : (
                          <Play className="h-4 w-4" />
                        )}

                        {selectedRun.action ===
                        "human_review"
                          ? "Send to Human Review"
                          : "Approve & Queue Execution"}
                      </button>

                      <button
                        type="button"
                        onClick={() =>
                          void rejectRun()
                        }
                        disabled={actionLoading}
                        className="flex flex-1 items-center justify-center gap-2 rounded-full border border-rose-200 bg-rose-50 px-5 py-3.5 text-sm font-medium text-rose-700 transition hover:-translate-y-0.5 hover:bg-rose-100 disabled:opacity-50"
                      >
                        <X className="h-4 w-4" />
                        Reject
                      </button>
                    </div>
                  </div>

                  <div className="flex gap-3 bg-slate-50/70 p-5">
                    <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />

                    <p className="text-xs leading-6 text-slate-500">
                      Approval changes the
                      persistent AgentRun state.
                      Executable actions are queued
                      through the durable integration
                      worker rather than executed
                      directly by the browser.
                    </p>
                  </div>
                </section>
              </div>
            )}
          </div>

          <section className="mt-7 overflow-hidden rounded-[22px] border border-violet-100 bg-gradient-to-r from-violet-50/65 via-white to-blue-50/55 p-6">
            <div className="flex flex-col justify-between gap-5 md:flex-row md:items-center">
              <div className="flex items-start gap-4">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-white text-violet-500 shadow-sm">
                  <Sparkles className="h-5 w-5" />
                </div>

                <div>
                  <h3 className="font-medium text-slate-900">
                    Human oversight remains
                    authoritative
                  </h3>

                  <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-500">
                    CXOps AI can recommend actions,
                    but protected external writes
                    remain governed by deterministic
                    authorization and reviewer
                    approval.
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 text-xs text-emerald-600">
                <CircleDot className="h-3.5 w-3.5" />
                Policy enforcement active
              </div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
