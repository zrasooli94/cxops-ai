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
    default:
      "bg-slate-800 text-slate-300",

    success:
      "bg-emerald-400/10 text-emerald-300",

    warning:
      "bg-amber-400/10 text-amber-300",

    danger:
      "bg-red-400/10 text-red-300",

    info:
      "bg-cyan-400/10 text-cyan-300",
  };

  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${styles[variant]}`}
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
):
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info" {
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

    default:
      return "default";
  }
}

function statusVariant(
  status: string,
):
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info" {
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

    default:
      return "info";
  }
}

function riskVariant(
  risk: ToolPlanItem["risk_level"],
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
        fetch(
          "/api/backend/tickets",
          {
            cache: "no-store",
          },
        ),

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
        if (current !== null) {
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
              active
            />

            <SidebarItem
              href="/approvals"
              icon={ShieldCheck}
              label="Approval Queue"
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
                Agent online
              </span>
            </div>

            <p className="mt-2 text-xs leading-5 text-slate-500">
              LangGraph + RAG + policy-based
              tool authorization
            </p>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="border-b border-slate-800 bg-[#090e18]/80 px-6 py-5 xl:px-10">
            <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
              <div>
                <p className="text-sm font-medium text-cyan-400">
                  Agentic AI Runtime
                </p>

                <h2 className="mt-1 text-2xl font-semibold">
                  AI Agent Console
                </h2>

                <p className="mt-1 text-sm text-slate-500">
                  Execute and inspect the live
                  LangGraph customer-operations
                  agent.
                </p>
              </div>

              <button
                onClick={() =>
                  void loadData()
                }
                disabled={loading}
                className="flex w-fit items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2 text-sm transition hover:bg-slate-800 disabled:opacity-50"
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
              <div className="mb-6 flex items-center gap-3 rounded-xl border border-red-900/50 bg-red-950/20 p-4 text-sm text-red-300">
                <XCircle className="h-5 w-5" />

                {error}
              </div>
            )}

            <section className="grid gap-6 xl:grid-cols-[420px_minmax(0,1fr)]">
              <div className="space-y-6">
                <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                  <div className="flex items-center gap-2">
                    <Bot className="h-5 w-5 text-cyan-400" />

                    <h3 className="font-semibold">
                      Run Agent
                    </h3>
                  </div>

                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    Select a live support ticket
                    and execute the production
                    agent workflow.
                  </p>

                  <div className="mt-6">
                    <label className="text-xs text-slate-500">
                      Ticket
                    </label>

                    <select
                      value={
                        ticketId ?? ""
                      }
                      onChange={(event) => {
                        setTicketId(
                          Number(
                            event.target.value,
                          ),
                        );

                        setAnalysis(null);
                      }}
                      className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none focus:border-cyan-500"
                    >
                      {tickets.map(
                        (ticket) => (
                          <option
                            key={
                              ticket.id
                            }
                            value={
                              ticket.id
                            }
                          >
                            #
                            {
                              ticket.id
                            }{" "}
                            —{" "}
                            {
                              ticket.subject
                            }
                          </option>
                        ),
                      )}
                    </select>
                  </div>

                  {selectedTicket && (
                    <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-4">
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
                      </div>

                      <p className="mt-4 font-medium">
                        {
                          selectedTicket.subject
                        }
                      </p>

                      <p className="mt-2 line-clamp-5 text-sm leading-6 text-slate-500">
                        {
                          selectedTicket.description
                        }
                      </p>

                      <div className="mt-4 border-t border-slate-800 pt-4 text-xs text-slate-600">
                        {
                          selectedTicket.requester_email
                        }
                      </div>
                    </div>
                  )}

                  <button
                    onClick={() =>
                      void runAgent()
                    }
                    disabled={
                      running ||
                      ticketId === null
                    }
                    className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-400 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:opacity-50"
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

                <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold">
                        Recent Runs
                      </h3>

                      <p className="mt-1 text-xs text-slate-500">
                        Latest persisted agent
                        decisions
                      </p>
                    </div>

                    <Badge>
                      {
                        recentRuns.length
                      }
                    </Badge>
                  </div>

                  <div className="mt-5 space-y-3">
                    {recentRuns.map(
                      (run) => (
                        <Link
                          key={
                            run.run_id
                          }
                          href="/runs"
                          className="block rounded-xl border border-slate-800 bg-slate-950/50 p-4 transition hover:border-slate-700"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <p className="text-xs text-slate-600">
                                Ticket #
                                {
                                  run.ticket_id
                                }
                              </p>

                              <p className="mt-1 text-sm font-medium">
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
                        </Link>
                      ),
                    )}
                  </div>
                </div>
              </div>

              <div className="min-w-0">
                {running && (
                  <div className="flex min-h-[650px] items-center justify-center rounded-2xl border border-cyan-900/40 bg-cyan-950/10">
                    <div className="max-w-md text-center">
                      <LoaderCircle className="mx-auto h-10 w-10 animate-spin text-cyan-400" />

                      <h3 className="mt-5 font-semibold">
                        LangGraph Agent Running
                      </h3>

                      <p className="mt-2 text-sm leading-6 text-slate-500">
                        Loading ticket → assessing
                        knowledge need → retrieving
                        policy evidence → choosing
                        action → building safe tool
                        plan.
                      </p>
                    </div>
                  </div>
                )}

                {!analysis &&
                  !running && (
                    <div className="flex min-h-[650px] items-center justify-center rounded-2xl border border-dashed border-slate-800 bg-slate-900/30">
                      <div className="max-w-lg text-center">
                        <Sparkles className="mx-auto h-10 w-10 text-slate-600" />

                        <h3 className="mt-4 font-semibold text-slate-300">
                          Agent Execution Console
                        </h3>

                        <p className="mt-2 text-sm leading-6 text-slate-500">
                          Choose a ticket and run the
                          agent to inspect its
                          reasoning, RAG evidence,
                          workflow path, authorization
                          policy and execution
                          decision.
                        </p>

                        <div className="mt-6 flex flex-wrap justify-center gap-2">
                          <Badge>
                            LangGraph
                          </Badge>

                          <Badge>
                            RAG
                          </Badge>

                          <Badge>
                            pgvector
                          </Badge>

                          <Badge>
                            Human-in-the-loop
                          </Badge>

                          <Badge>
                            Durable Queue
                          </Badge>
                        </div>
                      </div>
                    </div>
                  )}

                {analysis &&
                  !running && (
                    <div className="space-y-6">
                      <section className="rounded-2xl border border-cyan-900/40 bg-slate-900/60 p-6">
                        <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-start">
                          <div>
                            <div className="flex items-center gap-2">
                              <Sparkles className="h-5 w-5 text-cyan-400" />

                              <h3 className="font-semibold">
                                Agent Decision
                              </h3>
                            </div>

                            <p className="mt-2 break-all font-mono text-xs text-slate-600">
                              {
                                analysis.run_id
                              }
                            </p>
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

                            {analysis
                              .decision
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

                        <div className="mt-6 rounded-xl border border-slate-800 bg-slate-950/60 p-5">
                          <p className="text-xs uppercase tracking-wider text-slate-600">
                            Reason
                          </p>

                          <p className="mt-3 text-sm leading-7 text-slate-300">
                            {
                              analysis
                                .decision
                                .reason
                            }
                          </p>
                        </div>

                        <div className="mt-4 grid gap-4 md:grid-cols-2">
                          <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                            <p className="text-xs text-slate-500">
                              Recommended team
                            </p>

                            <p className="mt-2 font-medium">
                              {analysis
                                .decision
                                .recommended_team ??
                                "—"}
                            </p>
                          </div>

                          <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                            <p className="text-xs text-slate-500">
                              Recommended priority
                            </p>

                            <p className="mt-2 font-medium">
                              {analysis
                                .decision
                                .recommended_priority ??
                                "—"}
                            </p>
                          </div>
                        </div>

                        {analysis.decision
                          .response_draft && (
                          <div className="mt-4 rounded-xl border border-cyan-900/40 bg-cyan-950/10 p-5">
                            <p className="text-xs uppercase tracking-wider text-cyan-500">
                              Response draft
                            </p>

                            <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-300">
                              {
                                analysis
                                  .decision
                                  .response_draft
                              }
                            </p>
                          </div>
                        )}

                        {analysis.auto_queued && (
                          <div className="mt-4 flex gap-3 rounded-xl border border-emerald-900/40 bg-emerald-950/20 p-4">
                            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" />

                            <div>
                              <p className="text-sm font-medium text-emerald-300">
                                Autonomous execution
                                queued
                              </p>

                              <p className="mt-1 text-xs text-slate-500">
                                Integration job #
                                {
                                  analysis.job_id
                                }
                              </p>
                            </div>
                          </div>
                        )}

                        {analysis
                          .decision
                          .requires_human_approval && (
                          <div className="mt-4 flex gap-3 rounded-xl border border-amber-900/40 bg-amber-950/20 p-4">
                            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />

                            <div>
                              <p className="text-sm font-medium text-amber-300">
                                Human approval
                                checkpoint
                              </p>

                              <p className="mt-1 text-xs leading-5 text-slate-500">
                                External execution
                                remains blocked until
                                the run is reviewed in
                                the Approval Queue.
                              </p>
                            </div>
                          </div>
                        )}
                      </section>

                      <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                        <div className="flex items-center gap-2">
                          <Workflow className="h-5 w-5 text-cyan-400" />

                          <h3 className="font-semibold">
                            LangGraph Execution Path
                          </h3>
                        </div>

                        <div className="mt-5 flex flex-wrap items-center gap-2">
                          {analysis.workflow_path.map(
                            (
                              step,
                              index,
                            ) => (
                              <div
                                key={`${step}-${index}`}
                                className="flex items-center gap-2"
                              >
                                <span className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-300">
                                  {formatText(
                                    step,
                                  )}
                                </span>

                                {index <
                                  analysis
                                    .workflow_path
                                    .length -
                                    1 && (
                                  <span className="text-slate-600">
                                    →
                                  </span>
                                )}
                              </div>
                            ),
                          )}
                        </div>
                      </section>

                      <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                        <div className="flex items-center justify-between gap-4">
                          <div>
                            <div className="flex items-center gap-2">
                              <BrainCircuit className="h-5 w-5 text-cyan-400" />

                              <h3 className="font-semibold">
                                RAG Evidence
                              </h3>
                            </div>

                            <p className="mt-1 text-sm text-slate-500">
                              Policy evidence used
                              during agent reasoning.
                            </p>
                          </div>

                          <Badge variant="info">
                            {
                              analysis.sources
                                .length
                            }{" "}
                            sources
                          </Badge>
                        </div>

                        {analysis.sources.length ===
                        0 ? (
                          <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-5">
                            <p className="text-sm text-slate-500">
                              Knowledge retrieval was
                              not required for this
                              workflow.
                            </p>
                          </div>
                        ) : (
                          <div className="mt-5 space-y-4">
                            {analysis.sources.map(
                              (source) => (
                                <div
                                  key={`${source.source_id}-${source.chunk_id}`}
                                  className="rounded-xl border border-slate-800 bg-slate-950/50 p-5"
                                >
                                  <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                                    <div>
                                      <p className="font-medium">
                                        {
                                          source.source_id
                                        }{" "}
                                        —{" "}
                                        {
                                          source.title
                                        }
                                      </p>

                                      <p className="mt-1 text-xs text-slate-600">
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

                                  <p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-slate-400">
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

                      <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                        <div className="flex items-center justify-between">
                          <div>
                            <div className="flex items-center gap-2">
                              <ShieldCheck className="h-5 w-5 text-cyan-400" />

                              <h3 className="font-semibold">
                                Tool Authorization
                              </h3>
                            </div>

                            <p className="mt-1 text-sm text-slate-500">
                              Risk-controlled tools
                              proposed by the agent.
                            </p>
                          </div>

                          <Badge>
                            {
                              analysis.tool_plan
                                .length
                            }{" "}
                            tools
                          </Badge>
                        </div>

                        <div className="mt-5 space-y-4">
                          {analysis.tool_plan.map(
                            (
                              tool,
                              index,
                            ) => (
                              <div
                                key={`${tool.tool}-${index}`}
                                className="rounded-xl border border-slate-800 bg-slate-950/50 p-5"
                              >
                                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                                  <p className="font-mono text-sm text-cyan-300">
                                    {
                                      tool.tool
                                    }
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

                      <section className="grid gap-4 md:grid-cols-3">
                        <Link
                          href="/approvals"
                          className="flex items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-900 p-4 text-sm font-medium transition hover:bg-slate-800"
                        >
                          <ShieldCheck className="h-4 w-4 text-amber-400" />

                          Approval Queue
                        </Link>

                        <Link
                          href="/runs"
                          className="flex items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-900 p-4 text-sm font-medium transition hover:bg-slate-800"
                        >
                          <Workflow className="h-4 w-4 text-cyan-400" />

                          Audit Trail
                        </Link>

                        <Link
                          href="/observability"
                          className="flex items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-900 p-4 text-sm font-medium transition hover:bg-slate-800"
                        >
                          <Zap className="h-4 w-4 text-emerald-400" />

                          Observability
                        </Link>
                      </section>
                    </div>
                  )}
              </div>
            </section>

            <section className="mt-6 rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
              <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-center">
                <div>
                  <h3 className="font-semibold">
                    CXOps Agent Architecture
                  </h3>

                  <p className="mt-1 text-sm text-slate-500">
                    One production-style decision
                    pipeline with deterministic fast
                    paths, grounded RAG and
                    risk-based execution controls.
                  </p>
                </div>

                <Badge variant="success">
                  Live
                </Badge>
              </div>

              <div className="mt-6 flex flex-wrap items-center gap-2">
                {[
                  "Support Ticket",
                  "Knowledge Gate",
                  "pgvector Retrieval",
                  "LLM Decision",
                  "Tool Planning",
                  "Authorization",
                  "Human Review / Auto Queue",
                  "Durable Worker",
                  "Zendesk",
                ].map(
                  (item, index, items) => (
                    <div
                      key={item}
                      className="flex items-center gap-2"
                    >
                      <span className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300">
                        {item}
                      </span>

                      {index <
                        items.length - 1 && (
                        <span className="text-slate-600">
                          →
                        </span>
                      )}
                    </div>
                  ),
                )}
              </div>
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}