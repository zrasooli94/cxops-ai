"use client";

import {
  Activity,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Gauge,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Ticket,
  TriangleAlert,
  Workflow,
  X,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

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

function SidebarItem({
  href,
  icon: Icon,
  label,
  active = false,
}: {
  href: string;
  icon: typeof Activity;
  label: string;
  active?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${
        active
          ? "bg-cyan-400/10 text-cyan-300"
          : "text-slate-400 hover:bg-slate-900 hover:text-white"
      }`}
    >
      <Icon className="h-4 w-4" />
      {label}
    </Link>
  );
}

function Badge({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?:
    | "default"
    | "success"
    | "warning"
    | "danger"
    | "info";
}) {
  const styles = {
    default: "bg-slate-800 text-slate-300",
    success:
      "bg-emerald-400/10 text-emerald-300",
    warning:
      "bg-amber-400/10 text-amber-300",
    danger: "bg-red-400/10 text-red-300",
    info: "bg-cyan-400/10 text-cyan-300",
  };

  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

function riskVariant(
  risk: string,
):
  | "success"
  | "warning"
  | "danger" {
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
):
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info" {
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

export default function ApprovalsPage() {
  const [runs, setRuns] = useState<
    AgentRun[]
  >([]);

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

  const [actionLoading, setActionLoading] =
    useState(false);

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

        /*
         * Old development records may contain
         * no_action in pending_approval.
         * They are not executable approvals.
         */
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

      /*
       * human_review is a review state,
       * not an external Zendesk action.
       */
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
    <div className="min-h-screen bg-[#070b14] text-white">
      <div className="flex min-h-screen">
        <aside className="hidden w-64 shrink-0 border-r border-slate-800 bg-[#090e18] p-5 lg:block">
          <div className="mb-8 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-cyan-400 text-slate-950">
              <BrainCircuit className="h-6 w-6" />
            </div>

            <div>
              <h1 className="font-semibold">
                CXOps AI
              </h1>

              <p className="text-xs text-slate-500">
                Control Center
              </p>
            </div>
          </div>

          <nav className="space-y-1">
            <SidebarItem
              href="/"
              icon={Gauge}
              label="Operations"
            />

            <SidebarItem
              href="/tickets"
              icon={Ticket}
              label="Tickets"
            />

            <SidebarItem
              href="/agent"
              icon={Bot}
              label="AI Agent"
            />

            <SidebarItem
              href="/approvals"
              icon={ShieldCheck}
              label="Approval Queue"
              active
            />

            <SidebarItem
              href="/knowledge"
              icon={BrainCircuit}
              label="Knowledge / RAG"
            />

            <SidebarItem
              href="/runs"
              icon={Workflow}
              label="Agent Runs"
            />

            <SidebarItem
              href="/observability"
              icon={Activity}
              label="Observability"
            />
          </nav>

          <div className="mt-10 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <div className="flex items-center gap-2 text-sm">
              <div className="h-2.5 w-2.5 rounded-full bg-emerald-400" />

              <span>
                Safety controls active
              </span>
            </div>

            <p className="mt-2 text-xs leading-5 text-slate-500">
              Risk-based tool authorization
              and human oversight
            </p>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="border-b border-slate-800 bg-[#090e18]/80 px-6 py-5 xl:px-10">
            <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
              <div>
                <p className="text-sm font-medium text-cyan-400">
                  Human-in-the-loop
                </p>

                <h2 className="mt-1 text-2xl font-semibold">
                  Approval Queue
                </h2>

                <p className="mt-1 text-sm text-slate-500">
                  Review sensitive AI actions
                  before they reach external
                  systems.
                </p>
              </div>

              <button
                onClick={() =>
                  void loadData()
                }
                disabled={loading}
                className="flex w-fit items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2 text-sm"
              >
                <RefreshCw
                  className={`h-4 w-4 ${
                    loading
                      ? "animate-spin"
                      : ""
                  }`}
                />

                Refresh
              </button>
            </div>
          </header>

          <div className="p-6 xl:p-10">
            {error && (
              <div className="mb-5 flex items-center gap-3 rounded-xl border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">
                <XCircle className="h-5 w-5" />

                {error}
              </div>
            )}

            {success && (
              <div className="mb-5 flex items-center gap-3 rounded-xl border border-emerald-900/40 bg-emerald-950/20 p-4 text-sm text-emerald-300">
                <CheckCircle2 className="h-5 w-5" />

                {success}
              </div>
            )}

            <section className="mb-6 grid gap-4 md:grid-cols-3">
              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <p className="text-sm text-slate-500">
                  Awaiting approval
                </p>

                <p className="mt-2 text-3xl font-semibold text-amber-300">
                  {runs.length}
                </p>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <p className="text-sm text-slate-500">
                  High-risk runs
                </p>

                <p className="mt-2 text-3xl font-semibold text-red-300">
                  {
                    runs.filter((run) =>
                      run.tool_plan.some(
                        (tool) =>
                          tool.risk_level ===
                          "high",
                      ),
                    ).length
                  }
                </p>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <p className="text-sm text-slate-500">
                  Human review cases
                </p>

                <p className="mt-2 text-3xl font-semibold text-cyan-300">
                  {reviewRuns.length}
                </p>
              </div>
            </section>

            <div className="grid gap-6 xl:grid-cols-[390px_minmax(0,1fr)]">
              <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/60">
                <div className="border-b border-slate-800 p-4">
                  <div className="flex items-center justify-between">
                    <h3 className="font-medium">
                      Pending Decisions
                    </h3>

                    <Badge variant="warning">
                      {runs.length}
                    </Badge>
                  </div>
                </div>

                <div className="max-h-[750px] overflow-y-auto">
                  {loading ? (
                    <div className="flex h-40 items-center justify-center">
                      <LoaderCircle className="h-6 w-6 animate-spin text-cyan-400" />
                    </div>
                  ) : runs.length === 0 ? (
                    <div className="p-8 text-center">
                      <CheckCircle2 className="mx-auto h-8 w-8 text-emerald-400" />

                      <p className="mt-3 text-sm text-slate-400">
                        Approval queue is clear.
                      </p>
                    </div>
                  ) : (
                    runs.map((run) => {
                      const ticket =
                        ticketMap.get(
                          run.ticket_id,
                        );

                      return (
                        <button
                          key={run.run_id}
                          onClick={() => {
                            setSelectedRun(run);
                            setReviewNote("");
                            setError("");
                            setSuccess("");
                          }}
                          className={`w-full border-b border-slate-800 p-4 text-left transition ${
                            selectedRun?.run_id ===
                            run.run_id
                              ? "bg-cyan-400/10"
                              : "hover:bg-slate-800/50"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-xs text-slate-500">
                                Ticket #
                                {run.ticket_id}
                              </p>

                              <p className="mt-1 truncate text-sm font-medium">
                                {ticket?.subject ??
                                  formatText(
                                    run.action,
                                  )}
                              </p>
                            </div>

                            <Badge
                              variant={actionVariant(
                                run.action,
                              )}
                            >
                              {formatText(
                                run.action,
                              )}
                            </Badge>
                          </div>

                          <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">
                            {run.reason}
                          </p>
                        </button>
                      );
                    })
                  )}
                </div>
              </section>

              {!selectedRun ? (
                <div className="flex min-h-[500px] items-center justify-center rounded-2xl border border-slate-800 bg-slate-900/60">
                  <div className="text-center">
                    <ShieldCheck className="mx-auto h-9 w-9 text-slate-600" />

                    <p className="mt-3 text-sm text-slate-500">
                      Select an agent run.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="space-y-6">
                  <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="flex flex-col justify-between gap-4 md:flex-row md:items-start">
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

                        <h3 className="mt-4 text-xl font-semibold">
                          {selectedTicket?.subject ??
                            `Ticket #${selectedRun.ticket_id}`}
                        </h3>

                        <p className="mt-2 text-xs font-mono text-slate-600">
                          {selectedRun.run_id}
                        </p>
                      </div>

                      <div className="flex items-center gap-2 text-sm text-amber-300">
                        <ShieldAlert className="h-5 w-5" />

                        Awaiting reviewer
                      </div>
                    </div>

                    <div className="mt-6 rounded-xl border border-slate-800 bg-slate-950/60 p-5">
                      <p className="text-xs uppercase tracking-wider text-slate-500">
                        AI reasoning
                      </p>

                      <p className="mt-3 text-sm leading-7 text-slate-300">
                        {selectedRun.reason}
                      </p>
                    </div>

                    {selectedRun.response_draft && (
                      <div className="mt-4 rounded-xl border border-cyan-900/40 bg-cyan-950/10 p-5">
                        <p className="text-xs uppercase tracking-wider text-cyan-500">
                          Proposed customer reply
                        </p>

                        <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-300">
                          {
                            selectedRun.response_draft
                          }
                        </p>
                      </div>
                    )}
                  </section>

                  <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="flex items-center gap-2">
                      <Workflow className="h-5 w-5 text-cyan-400" />

                      <h3 className="font-semibold">
                        Proposed Tool Plan
                      </h3>
                    </div>

                    <div className="mt-5 space-y-4">
                      {selectedRun.tool_plan.map(
                        (tool, index) => (
                          <div
                            key={`${tool.tool}-${index}`}
                            className="rounded-xl border border-slate-800 bg-slate-950/50 p-5"
                          >
                            <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                              <p className="font-mono text-sm text-cyan-300">
                                {tool.tool}
                              </p>

                              <div className="flex flex-wrap gap-2">
                                <Badge
                                  variant={riskVariant(
                                    tool.risk_level,
                                  )}
                                >
                                  {
                                    tool.risk_level
                                  }{" "}
                                  risk
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
                              tool.arguments ??
                                {},
                            ).length > 0 && (
                              <pre className="mt-4 overflow-x-auto rounded-lg bg-[#070b14] p-4 text-xs leading-6 text-slate-400">
                                {JSON.stringify(
                                  tool.arguments,
                                  null,
                                  2,
                                )}
                              </pre>
                            )}
                          </div>
                        ),
                      )}
                    </div>
                  </section>

                  <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <h3 className="font-semibold">
                      Reviewer Decision
                    </h3>

                    <p className="mt-1 text-sm text-slate-500">
                      Record the reasoning behind
                      this approval decision.
                    </p>

                    <textarea
                      value={reviewNote}
                      onChange={(event) =>
                        setReviewNote(
                          event.target.value,
                        )
                      }
                      rows={4}
                      placeholder="Reviewer note..."
                      className="mt-5 w-full resize-none rounded-xl border border-slate-700 bg-slate-950 p-4 text-sm outline-none transition placeholder:text-slate-600 focus:border-cyan-500"
                    />

                    <div className="mt-5 flex flex-col gap-3 sm:flex-row">
                      <button
                        onClick={() =>
                          void approveAndQueue()
                        }
                        disabled={actionLoading}
                        className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-400 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-emerald-300 disabled:opacity-50"
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
                        onClick={() =>
                          void rejectRun()
                        }
                        disabled={actionLoading}
                        className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-red-900/60 bg-red-950/20 px-5 py-3 text-sm font-semibold text-red-300 transition hover:bg-red-950/40 disabled:opacity-50"
                      >
                        <X className="h-4 w-4" />

                        Reject
                      </button>
                    </div>

                    <div className="mt-5 flex gap-3 rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                      <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />

                      <p className="text-xs leading-5 text-slate-500">
                        Approval changes the
                        persistent AgentRun state.
                        Executable actions are then
                        queued through the durable
                        integration worker rather
                        than executed directly by
                        the browser.
                      </p>
                    </div>
                  </section>
                </div>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}