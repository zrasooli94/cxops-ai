"use client";

import {
  ArrowRight,
  Bot,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Cpu,
  Database,
  LoaderCircle,
  Network,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Ticket,
  Workflow,
  XCircle,
  Zap,
} from "lucide-react";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import AppSidebar from "@/components/app-sidebar";

type TicketRecord = {
  id: number;
  external_id: string | null;
  subject: string;
  description: string;
  status: string;
  priority: string | null;
  requester_email: string;
  source: string;
  category: string | null;
  assigned_team: string | null;
};

type AgentDecision = {
  action:
    | "respond"
    | "route"
    | "escalate"
    | "internal_note"
    | "human_review"
    | "no_action";
  reason: string;
  recommended_team: string | null;
  recommended_priority:
    | "low"
    | "normal"
    | "high"
    | "urgent"
    | null;
  response_draft: string | null;
  requires_human_approval: boolean;
};

type KnowledgeSource = {
  source_id: string;
  chunk_id: number;
  document_id: number;
  title: string;
  content: string;
  similarity: number;
};

type ToolPlanItem = {
  tool: string;
  arguments: Record<string, unknown>;
  risk_level: "low" | "medium" | "high";
  requires_approval: boolean;
  authorized: boolean;
};

type AgentAnalysis = {
  run_id: string;
  ticket_id: number;
  decision: AgentDecision;
  sources: KnowledgeSource[];
  workflow_path: string[];
  tool_plan: ToolPlanItem[];
  auto_queued: boolean;
  job_id: number | null;
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

function formatText(value: string) {
  return value
    .replaceAll("_", " ")
    .split(" ")
    .map(
      (part) =>
        part.charAt(0).toUpperCase() +
        part.slice(1),
    )
    .join(" ");
}

function actionVariant(
  action: string,
): BadgeVariant {
  switch (action) {
    case "escalate":
      return "danger";
    case "human_review":
      return "warning";
    case "respond":
    case "route":
      return "info";
    case "internal_note":
      return "success";
    case "no_action":
      return "default";
    default:
      return "default";
  }
}

function statusVariant(
  status: string,
): BadgeVariant {
  switch (status) {
    case "executed":
    case "approved":
      return "success";
    case "pending_approval":
    case "review_required":
    case "executing":
      return "warning";
    case "rejected":
    case "execution_failed":
      return "danger";
    case "superseded":
      return "default";
    default:
      return "info";
  }
}

function riskVariant(
  risk: ToolPlanItem["risk_level"],
): BadgeVariant {
  if (risk === "high") {
    return "danger";
  }

  if (risk === "medium") {
    return "warning";
  }

  return "success";
}

function MiniMetric({
  label,
  value,
  tone = "violet",
}: {
  label: string;
  value: string | number;
  tone?: "violet" | "blue" | "emerald";
}) {
  const iconTone = {
    violet: "bg-violet-50 text-violet-500",
    blue: "bg-blue-50 text-blue-500",
    emerald:
      "bg-emerald-50 text-emerald-500",
  }[tone];

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white/70 p-4">
      <div
        className={`mb-4 h-2 w-2 rounded-full ${iconTone}`}
      />

      <p className="text-[10px] uppercase tracking-[0.13em] text-slate-400">
        {label}
      </p>

      <p className="editorial-number mt-2 text-2xl font-medium tracking-[-0.04em] text-slate-950">
        {value}
      </p>
    </div>
  );
}

export default function AgentPage() {
  const [tickets, setTickets] =
    useState<TicketRecord[]>([]);

  const [recentRuns, setRecentRuns] =
    useState<AgentRun[]>([]);

  const [ticketId, setTicketId] =
    useState<number | null>(null);

  const [analysis, setAnalysis] =
    useState<AgentAnalysis | null>(null);

  const [loading, setLoading] =
    useState(true);

  const [running, setRunning] =
    useState(false);

  const [error, setError] =
    useState("");

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [
        ticketResponse,
        runResponse,
      ] = await Promise.all([
        fetch("/api/backend/tickets", {
          cache: "no-store",
        }),
        fetch(
          "/api/backend/agent/runs?limit=8",
          {
            cache: "no-store",
          },
        ),
      ]);

      if (
        !ticketResponse.ok ||
        !runResponse.ok
      ) {
        throw new Error(
          "Failed to load agent console data.",
        );
      }

      const ticketData =
        await ticketResponse.json();

      const ticketRows: TicketRecord[] =
        Array.isArray(ticketData)
          ? ticketData
          : Array.isArray(ticketData.items)
            ? ticketData.items
            : [];

      const runRows =
        (await runResponse.json()) as AgentRun[];

      setTickets(ticketRows);
      setRecentRuns(runRows);

      setTicketId((current) => {
        if (
          current !== null &&
          ticketRows.some(
            (ticket) =>
              ticket.id === current,
          )
        ) {
          return current;
        }

        return ticketRows[0]?.id ?? null;
      });
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to load agent console.",
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

  const selectedTicket = useMemo(
    () =>
      tickets.find(
        (ticket) =>
          ticket.id === ticketId,
      ) ?? null,
    [tickets, ticketId],
  );

  const approvalRuns = useMemo(
    () =>
      recentRuns.filter(
        (run) =>
          run.status ===
            "pending_approval" ||
          run.status ===
            "review_required",
      ).length,
    [recentRuns],
  );

  async function runAgent() {
    if (!ticketId) {
      return;
    }

    setRunning(true);
    setError("");
    setAnalysis(null);

    try {
      const response = await fetch(
        `/api/backend/agent/tickets/${ticketId}/analyze`,
        {
          method: "POST",
          cache: "no-store",
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        throw new Error(
          body?.detail ??
            `Agent API returned ${response.status}`,
        );
      }

      setAnalysis(
        body as AgentAnalysis,
      );

      const recentResponse =
        await fetch(
          "/api/backend/agent/runs?limit=8",
          {
            cache: "no-store",
          },
        );

      if (recentResponse.ok) {
        setRecentRuns(
          (await recentResponse.json()) as AgentRun[],
        );
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Agent execution failed.",
      );
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="min-h-screen">
      <AppSidebar active="/agent" />

      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                AI Agent
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Agentic AI execution console
              </p>
            </div>

            <div className="flex items-center gap-3">
              <div className="hidden items-center gap-2 rounded-full border border-slate-200 bg-white/80 px-4 py-2 text-[11px] text-slate-500 shadow-sm sm:flex">
                <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.65)]" />
                Agent online
              </div>

              <button
                type="button"
                onClick={() =>
                  void loadData()
                }
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh agent console"
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
            <div className="flex flex-col justify-between gap-8 lg:flex-row lg:items-end">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
                  Agentic AI Runtime
                </p>

                <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
                  AI Agent Console
                </h1>

                <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
                  Execute and inspect the live
                  LangGraph customer-operations
                  agent with grounded RAG,
                  deterministic policy checks and
                  risk-based tool authorization.
                </p>
              </div>

              <div className="grid w-full gap-3 sm:grid-cols-3 lg:w-auto lg:min-w-[470px]">
                <MiniMetric
                  label="Tickets"
                  value={tickets.length}
                />

                <MiniMetric
                  label="Recent runs"
                  value={recentRuns.length}
                  tone="blue"
                />

                <MiniMetric
                  label="Needs review"
                  value={approvalRuns}
                  tone="emerald"
                />
              </div>
            </div>
          </section>

          {error && (
            <div className="mb-7 flex items-start gap-3 rounded-[20px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
              {error}
            </div>
          )}

          <section className="grid gap-6 xl:grid-cols-[390px_minmax(0,1fr)]">
            <div className="space-y-6">
              <div className="app-panel rounded-[22px] p-6">
                <div className="flex items-center gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-blue-500 text-white shadow-[0_12px_28px_rgba(105,87,255,0.2)]">
                    <Bot className="h-5 w-5" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-950">
                      Run Agent
                    </h2>

                    <p className="text-xs text-slate-400">
                      Live production workflow
                    </p>
                  </div>
                </div>

                <p className="mt-5 text-sm leading-6 text-slate-500">
                  Select a support ticket and
                  execute the complete CXOps
                  decision pipeline.
                </p>

                <div className="mt-6">
                  <label className="text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                    Ticket
                  </label>

                  <select
                    value={ticketId ?? ""}
                    onChange={(event) => {
                      setTicketId(
                        Number(
                          event.target.value,
                        ),
                      );
                      setAnalysis(null);
                    }}
                    className="mt-2 w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-800 outline-none transition focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                  >
                    {tickets.map(
                      (ticket) => (
                        <option
                          key={ticket.id}
                          value={ticket.id}
                        >
                          #{ticket.id} —{" "}
                          {ticket.subject}
                        </option>
                      ),
                    )}
                  </select>
                </div>

                {selectedTicket && (
                  <div className="mt-5 overflow-hidden rounded-2xl border border-slate-200/80 bg-gradient-to-br from-[#fbfcff] to-violet-50/30 p-5">
                    <div className="flex flex-wrap gap-2">
                      <Badge>
                        {
                          selectedTicket.status
                        }
                      </Badge>

                      <Badge
                        variant={
                          selectedTicket.priority ===
                            "urgent" ||
                          selectedTicket.priority ===
                            "high"
                            ? "danger"
                            : "warning"
                        }
                      >
                        {selectedTicket.priority ??
                          "no priority"}
                      </Badge>

                      {selectedTicket.external_id && (
                        <Badge variant="info">
                          Zendesk #
                          {
                            selectedTicket.external_id
                          }
                        </Badge>
                      )}
                    </div>

                    <p className="mt-4 font-medium leading-6 text-slate-900">
                      {
                        selectedTicket.subject
                      }
                    </p>

                    <p className="mt-2 line-clamp-5 text-sm leading-6 text-slate-500">
                      {
                        selectedTicket.description
                      }
                    </p>

                    <div className="mt-4 border-t border-slate-200/70 pt-4">
                      <p className="truncate text-xs text-slate-400">
                        {
                          selectedTicket.requester_email
                        }
                      </p>

                      <div className="mt-3 flex flex-wrap gap-2">
                        {selectedTicket.category && (
                          <Badge variant="violet">
                            {
                              selectedTicket.category
                            }
                          </Badge>
                        )}

                        {selectedTicket.assigned_team && (
                          <Badge>
                            {
                              selectedTicket.assigned_team
                            }
                          </Badge>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                <button
                  type="button"
                  onClick={() =>
                    void runAgent()
                  }
                  disabled={
                    running ||
                    ticketId === null
                  }
                  className="mt-5 flex w-full items-center justify-center gap-2.5 rounded-full bg-[#111827] px-5 py-3.5 text-sm font-medium text-white shadow-[0_12px_30px_rgba(17,24,39,0.15)] transition hover:-translate-y-0.5 hover:bg-gradient-to-r hover:from-[#765cff] hover:to-[#508cff] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {running ? (
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}

                  {running
                    ? "Agent running..."
                    : "Run CXOps Agent"}
                </button>
              </div>

              <div className="app-panel overflow-hidden rounded-[22px]">
                <div className="flex items-center justify-between border-b border-slate-200/70 p-5">
                  <div>
                    <h2 className="font-medium text-slate-900">
                      Recent Runs
                    </h2>

                    <p className="mt-1 text-xs text-slate-400">
                      Latest persisted decisions
                    </p>
                  </div>

                  <Badge variant="violet">
                    {recentRuns.length}
                  </Badge>
                </div>

                <div
                  data-lenis-prevent
                  className="max-h-[520px] overflow-y-auto overscroll-contain"
                >
                  {recentRuns.length === 0 ? (
                    <div className="p-8 text-center text-sm text-slate-400">
                      No agent runs yet.
                    </div>
                  ) : (
                    recentRuns.map(
                      (run) => (
                        <Link
                          key={run.run_id}
                          href="/runs"
                          className="group block border-b border-slate-200/60 p-5 transition last:border-0 hover:bg-violet-50/35"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-slate-400">
                                Ticket #
                                {
                                  run.ticket_id
                                }
                              </p>

                              <p className="mt-2 text-sm font-medium text-slate-800">
                                {formatText(
                                  run.action,
                                )}
                              </p>
                            </div>

                            <Badge
                              variant={statusVariant(
                                run.status,
                              )}
                            >
                              {formatText(
                                run.status,
                              )}
                            </Badge>
                          </div>

                          <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">
                            {run.reason}
                          </p>

                          <div className="mt-3 flex items-center justify-end">
                            <ChevronRight className="h-4 w-4 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-violet-500" />
                          </div>
                        </Link>
                      ),
                    )
                  )}
                </div>
              </div>
            </div>

            <div className="min-w-0">
              {running && (
                <div className="app-panel relative flex min-h-[650px] overflow-hidden rounded-[22px]">
                  <div className="absolute inset-0 bg-gradient-to-br from-violet-50/80 via-white to-blue-50/60" />

                  <div className="soft-grid absolute inset-0 opacity-30" />

                  <div className="relative m-auto max-w-md px-8 text-center">
                    <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[22px] bg-white shadow-[0_20px_55px_rgba(98,82,255,0.14)]">
                      <LoaderCircle className="h-8 w-8 animate-spin text-violet-500" />
                    </div>

                    <h3 className="mt-6 text-xl font-medium tracking-[-0.03em] text-slate-950">
                      LangGraph Agent Running
                    </h3>

                    <p className="mt-3 text-sm leading-7 text-slate-500">
                      Loading ticket → assessing
                      knowledge need → retrieving
                      policy evidence → choosing
                      action → building a safe
                      authorization plan.
                    </p>

                    <div className="mt-7 flex flex-wrap justify-center gap-2">
                      {[
                        "Ticket",
                        "Knowledge",
                        "RAG",
                        "Decision",
                        "Authorization",
                      ].map((item) => (
                        <span
                          key={item}
                          className="rounded-full border border-violet-100 bg-white/80 px-3 py-1.5 text-[10px] font-medium text-violet-600"
                        >
                          {item}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {!analysis &&
                !running && (
                  <div className="app-panel relative flex min-h-[650px] overflow-hidden rounded-[22px]">
                    <div className="pointer-events-none absolute -right-32 -top-28 h-96 w-96 rounded-full bg-violet-300/10 blur-3xl" />

                    <div className="pointer-events-none absolute -bottom-40 -left-32 h-96 w-96 rounded-full bg-blue-300/10 blur-3xl" />

                    <div className="soft-grid absolute inset-0 opacity-20" />

                    <div className="relative m-auto max-w-2xl px-8 py-14 text-center">
                      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[22px] bg-gradient-to-br from-violet-500 to-blue-500 text-white shadow-[0_20px_55px_rgba(98,82,255,0.22)]">
                        <Sparkles className="h-7 w-7" />
                      </div>

                      <p className="mt-7 text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-500">
                        Agent Execution Console
                      </p>

                      <h2 className="mt-4 text-3xl font-light tracking-[-0.05em] text-slate-950 md:text-4xl">
                        See every decision
                        before it becomes an
                        action.
                      </h2>

                      <p className="mx-auto mt-4 max-w-xl text-sm leading-7 text-slate-500">
                        Choose a ticket and run the
                        agent to inspect the
                        decision, RAG evidence,
                        LangGraph path, authorization
                        policy and external execution
                        eligibility.
                      </p>

                      <div className="mt-8 flex flex-wrap justify-center gap-2">
                        <Badge variant="violet">
                          LangGraph
                        </Badge>
                        <Badge variant="info">
                          RAG
                        </Badge>
                        <Badge>
                          pgvector
                        </Badge>
                        <Badge variant="warning">
                          Human-in-the-loop
                        </Badge>
                        <Badge variant="success">
                          Durable Queue
                        </Badge>
                      </div>
                    </div>
                  </div>
                )}

              {analysis &&
                !running && (
                  <div className="space-y-6">
                    <section className="app-panel overflow-hidden rounded-[22px]">
                      <div className="border-b border-slate-200/70 bg-gradient-to-r from-violet-50/75 via-white to-blue-50/60 p-6 md:p-7">
                        <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-start">
                          <div className="flex items-center gap-3">
                            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-blue-500 text-white shadow-[0_10px_28px_rgba(101,85,255,0.18)]">
                              <Sparkles className="h-5 w-5" />
                            </div>

                            <div>
                              <h2 className="font-medium text-slate-950">
                                Agent Decision
                              </h2>

                              <p className="mt-1 break-all font-mono text-[10px] text-slate-400">
                                {
                                  analysis.run_id
                                }
                              </p>
                            </div>
                          </div>

                          <div className="flex flex-wrap gap-2">
                            <Badge
                              variant={actionVariant(
                                analysis
                                  .decision
                                  .action,
                              )}
                            >
                              {formatText(
                                analysis
                                  .decision
                                  .action,
                              )}
                            </Badge>

                            {analysis.decision
                              .requires_human_approval ? (
                              <Badge variant="warning">
                                Human approval
                              </Badge>
                            ) : (
                              <Badge variant="success">
                                No approval needed
                              </Badge>
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="p-6 md:p-7">
                        <div className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5">
                          <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                            Reason
                          </p>

                          <p className="mt-3 text-sm leading-7 text-slate-600">
                            {
                              analysis.decision
                                .reason
                            }
                          </p>
                        </div>

                        <div className="mt-4 grid gap-4 md:grid-cols-2">
                          <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                            <p className="text-xs text-slate-400">
                              Recommended team
                            </p>

                            <p className="mt-2 font-medium text-slate-900">
                              {analysis.decision
                                .recommended_team ??
                                "—"}
                            </p>
                          </div>

                          <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                            <p className="text-xs text-slate-400">
                              Recommended priority
                            </p>

                            <p className="mt-2 font-medium capitalize text-slate-900">
                              {analysis.decision
                                .recommended_priority ??
                                "—"}
                            </p>
                          </div>
                        </div>

                        {analysis.decision
                          .response_draft && (
                          <div className="mt-4 rounded-2xl border border-blue-100 bg-blue-50/45 p-5">
                            <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-blue-500">
                              Response draft
                            </p>

                            <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-600">
                              {
                                analysis
                                  .decision
                                  .response_draft
                              }
                            </p>
                          </div>
                        )}

                        {analysis.auto_queued && (
                          <div className="mt-4 flex gap-3 rounded-2xl border border-emerald-200 bg-emerald-50/70 p-4">
                            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />

                            <div>
                              <p className="text-sm font-medium text-emerald-800">
                                Autonomous execution
                                queued
                              </p>

                              <p className="mt-1 text-xs text-emerald-600">
                                Integration job #
                                {
                                  analysis.job_id
                                }
                              </p>
                            </div>
                          </div>
                        )}

                        {analysis.decision
                          .requires_human_approval && (
                          <div className="mt-4 flex gap-3 rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
                            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />

                            <div>
                              <p className="text-sm font-medium text-amber-800">
                                Human approval
                                checkpoint
                              </p>

                              <p className="mt-1 text-xs leading-5 text-amber-700">
                                External execution
                                remains blocked until
                                the run is reviewed in
                                the Approval Queue.
                              </p>
                            </div>
                          </div>
                        )}
                      </div>
                    </section>

                    <section className="app-panel rounded-[22px] p-6 md:p-7">
                      <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                          <Network className="h-5 w-5" />
                        </div>

                        <div>
                          <h2 className="font-medium text-slate-900">
                            LangGraph Execution Path
                          </h2>

                          <p className="text-xs text-slate-400">
                            Nodes traversed during
                            this run
                          </p>
                        </div>
                      </div>

                      <div className="mt-6 flex flex-wrap items-center gap-2">
                        {analysis.workflow_path.map(
                          (
                            step,
                            index,
                          ) => (
                            <div
                              key={`${step}-${index}`}
                              className="flex items-center gap-2"
                            >
                              <span className="rounded-xl border border-violet-100 bg-gradient-to-br from-white to-violet-50/70 px-3.5 py-2 text-xs font-medium text-slate-700 shadow-sm">
                                {formatText(
                                  step,
                                )}
                              </span>

                              {index <
                                analysis
                                  .workflow_path
                                  .length -
                                  1 && (
                                <ChevronRight className="h-4 w-4 text-violet-300" />
                              )}
                            </div>
                          ),
                        )}
                      </div>
                    </section>

                    <section className="app-panel rounded-[22px] p-6 md:p-7">
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                            <BrainCircuit className="h-5 w-5" />
                          </div>

                          <div>
                            <h2 className="font-medium text-slate-900">
                              RAG Evidence
                            </h2>

                            <p className="text-xs text-slate-400">
                              Policy evidence used
                              during reasoning
                            </p>
                          </div>
                        </div>

                        <Badge variant="info">
                          {
                            analysis.sources
                              .length
                          }{" "}
                          source
                          {analysis.sources
                            .length === 1
                            ? ""
                            : "s"}
                        </Badge>
                      </div>

                      {analysis.sources.length ===
                      0 ? (
                        <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50/70 p-5">
                          <p className="text-sm text-slate-500">
                            Knowledge retrieval was
                            not required for this
                            workflow.
                          </p>
                        </div>
                      ) : (
                        <div className="mt-6 space-y-4">
                          {analysis.sources.map(
                            (source) => (
                              <div
                                key={`${source.source_id}-${source.chunk_id}`}
                                className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5"
                              >
                                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                                  <div>
                                    <p className="font-medium text-slate-800">
                                      {
                                        source.source_id
                                      }{" "}
                                      —{" "}
                                      {
                                        source.title
                                      }
                                    </p>

                                    <p className="mt-1 text-xs text-slate-400">
                                      Document{" "}
                                      {
                                        source.document_id
                                      }{" "}
                                      · Chunk{" "}
                                      {
                                        source.chunk_id
                                      }
                                    </p>
                                  </div>

                                  <Badge variant="success">
                                    {(
                                      source.similarity *
                                      100
                                    ).toFixed(
                                      1,
                                    )}
                                    %
                                  </Badge>
                                </div>

                                <p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-slate-500">
                                  {
                                    source.content
                                  }
                                </p>
                              </div>
                            ),
                          )}
                        </div>
                      )}
                    </section>

                    <section className="app-panel rounded-[22px] p-6 md:p-7">
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
                            <ShieldCheck className="h-5 w-5" />
                          </div>

                          <div>
                            <h2 className="font-medium text-slate-900">
                              Tool Authorization
                            </h2>

                            <p className="text-xs text-slate-400">
                              Risk-controlled tools
                              proposed by the agent
                            </p>
                          </div>
                        </div>

                        <Badge variant="violet">
                          {
                            analysis.tool_plan
                              .length
                          }{" "}
                          tool
                          {analysis.tool_plan
                            .length === 1
                            ? ""
                            : "s"}
                        </Badge>
                      </div>

                      <div className="mt-6 space-y-4">
                        {analysis.tool_plan.map(
                          (
                            tool,
                            index,
                          ) => (
                            <div
                              key={`${tool.tool}-${index}`}
                              className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5"
                            >
                              <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                                <div>
                                  <p className="font-mono text-sm font-medium text-violet-600">
                                    {
                                      tool.tool
                                    }
                                  </p>

                                  <p className="mt-1 text-[10px] uppercase tracking-[0.1em] text-slate-400">
                                    Tool{" "}
                                    {index + 1}
                                  </p>
                                </div>

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
                                      tool.authorized
                                        ? "success"
                                        : "warning"
                                    }
                                  >
                                    {tool.authorized
                                      ? "Authorized"
                                      : "Locked"}
                                  </Badge>

                                  {tool.requires_approval && (
                                    <Badge variant="warning">
                                      Approval
                                      required
                                    </Badge>
                                  )}
                                </div>
                              </div>

                              {Object.keys(
                                tool.arguments ??
                                  {},
                              ).length >
                                0 && (
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

                    <section className="grid gap-4 md:grid-cols-3">
                      <Link
                        href="/approvals"
                        className="app-panel group flex items-center justify-between rounded-[18px] p-5 transition hover:-translate-y-0.5 hover:border-amber-200"
                      >
                        <div className="flex items-center gap-3">
                          <ShieldCheck className="h-5 w-5 text-amber-500" />

                          <span className="text-sm font-medium text-slate-800">
                            Approval Queue
                          </span>
                        </div>

                        <ArrowRight className="h-4 w-4 text-slate-300 transition group-hover:translate-x-1 group-hover:text-amber-500" />
                      </Link>

                      <Link
                        href="/runs"
                        className="app-panel group flex items-center justify-between rounded-[18px] p-5 transition hover:-translate-y-0.5 hover:border-violet-200"
                      >
                        <div className="flex items-center gap-3">
                          <Workflow className="h-5 w-5 text-violet-500" />

                          <span className="text-sm font-medium text-slate-800">
                            Audit Trail
                          </span>
                        </div>

                        <ArrowRight className="h-4 w-4 text-slate-300 transition group-hover:translate-x-1 group-hover:text-violet-500" />
                      </Link>

                      <Link
                        href="/observability"
                        className="app-panel group flex items-center justify-between rounded-[18px] p-5 transition hover:-translate-y-0.5 hover:border-emerald-200"
                      >
                        <div className="flex items-center gap-3">
                          <Zap className="h-5 w-5 text-emerald-500" />

                          <span className="text-sm font-medium text-slate-800">
                            Observability
                          </span>
                        </div>

                        <ArrowRight className="h-4 w-4 text-slate-300 transition group-hover:translate-x-1 group-hover:text-emerald-500" />
                      </Link>
                    </section>
                  </div>
                )}
            </div>
          </section>

          <section className="app-panel mt-7 overflow-hidden rounded-[22px]">
            <div className="grid lg:grid-cols-[0.75fr_1.25fr]">
              <div className="border-b border-slate-200/70 bg-gradient-to-br from-violet-50/75 to-blue-50/45 p-7 lg:border-b-0 lg:border-r">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white text-violet-500 shadow-sm">
                    <Cpu className="h-5 w-5" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-900">
                      CXOps Agent Architecture
                    </h2>

                    <p className="text-xs text-slate-400">
                      Production decision pipeline
                    </p>
                  </div>
                </div>

                <p className="mt-6 max-w-md text-sm leading-7 text-slate-500">
                  Deterministic fast paths,
                  grounded retrieval, structured
                  AI decisions and explicit
                  authorization gates before any
                  external tool can execute.
                </p>

                <div className="mt-6 flex items-center gap-2 text-xs text-emerald-600">
                  <CircleDot className="h-3.5 w-3.5" />
                  Live
                </div>
              </div>

              <div className="p-7">
                <div className="flex flex-wrap items-center gap-2">
                  {[
                    {
                      name: "Support Ticket",
                      icon: Ticket,
                    },
                    {
                      name: "Knowledge Gate",
                      icon: BrainCircuit,
                    },
                    {
                      name: "pgvector Retrieval",
                      icon: Database,
                    },
                    {
                      name: "LLM Decision",
                      icon: Sparkles,
                    },
                    {
                      name: "Tool Planning",
                      icon: Workflow,
                    },
                    {
                      name: "Authorization",
                      icon: ShieldCheck,
                    },
                    {
                      name: "Review / Auto Queue",
                      icon: Bot,
                    },
                    {
                      name: "Durable Worker",
                      icon: Cpu,
                    },
                    {
                      name: "Zendesk",
                      icon: Zap,
                    },
                  ].map(
                    (
                      item,
                      index,
                      items,
                    ) => {
                      const Icon =
                        item.icon;

                      return (
                        <div
                          key={
                            item.name
                          }
                          className="flex items-center gap-2"
                        >
                          <span className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 shadow-sm">
                            <Icon className="h-3.5 w-3.5 text-violet-500" />
                            {
                              item.name
                            }
                          </span>

                          {index <
                            items.length -
                              1 && (
                            <ChevronRight className="h-4 w-4 text-slate-300" />
                          )}
                        </div>
                      );
                    },
                  )}
                </div>
              </div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
