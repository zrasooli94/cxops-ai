"use client";

import {
  Activity,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Gauge,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Ticket as TicketIcon,
  TriangleAlert,
  User,
  Workflow,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

type Ticket = {
  id: number;
  external_id: string | null;
  subject: string;
  description: string;
  status: string;
  priority: string | null;
  requester_email: string;
  source: string;
  created_at: string;
  updated_at: string;
  customer_id: number | null;
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

function priorityVariant(
  priority: string | null,
):
  | "default"
  | "success"
  | "warning"
  | "danger" {
  switch (priority?.toLowerCase()) {
    case "urgent":
    case "high":
      return "danger";

    case "normal":
      return "warning";

    case "low":
      return "success";

    default:
      return "default";
  }
}

function actionVariant(
  action: AgentDecision["action"],
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

    case "no_action":
      return "default";
  }
}

function riskVariant(
  risk: ToolPlanItem["risk_level"],
):
  | "success"
  | "warning"
  | "danger" {
  switch (risk) {
    case "high":
      return "danger";

    case "medium":
      return "warning";

    default:
      return "success";
  }
}

function formatAction(action: string) {
  return action
    .split("_")
    .map(
      (part) =>
        part.charAt(0).toUpperCase() +
        part.slice(1),
    )
    .join(" ");
}

function formatWorkflowStep(step: string) {
  return step
    .split("_")
    .map(
      (part) =>
        part.charAt(0).toUpperCase() +
        part.slice(1),
    )
    .join(" ");
}

export default function TicketsPage() {
  const [tickets, setTickets] = useState<
    Ticket[]
  >([]);

  const [
    selectedTicket,
    setSelectedTicket,
  ] = useState<Ticket | null>(null);

  const [search, setSearch] =
    useState("");

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState("");

  const [analysis, setAnalysis] =
    useState<AgentAnalysis | null>(null);

  const [
    analyzing,
    setAnalyzing,
  ] = useState(false);

  const [
    analysisError,
    setAnalysisError,
  ] = useState("");

  const loadTickets =
    useCallback(async () => {
      setLoading(true);
      setError("");

      try {
        const response = await fetch(
          "/api/backend/tickets",
          {
            cache: "no-store",
          },
        );

        if (!response.ok) {
          throw new Error(
            `Ticket API returned ${response.status}`,
          );
        }

        const data =
          await response.json();

        const rows: Ticket[] =
          Array.isArray(data)
            ? data
            : Array.isArray(data.items)
              ? data.items
              : [];

        setTickets(rows);

        if (rows.length > 0) {
          setSelectedTicket(
            (current) =>
              current ?? rows[0],
          );
        }
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load tickets.",
        );
      } finally {
        setLoading(false);
      }
    }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadTickets();
    }, 0);
  
    return () => {
      window.clearTimeout(timer);
    };
  }, [loadTickets]);

  function selectTicket(ticket: Ticket) {
    setSelectedTicket(ticket);

    setAnalysis(null);
    setAnalysisError("");
  }

  async function analyzeTicket() {
    if (!selectedTicket) {
      return;
    }

    setAnalyzing(true);
    setAnalysis(null);
    setAnalysisError("");

    try {
      const response = await fetch(
        `/api/backend/agent/tickets/${selectedTicket.id}/analyze`,
        {
          method: "POST",
          cache: "no-store",
        },
      );

      const body = await response.json();

      if (!response.ok) {
        throw new Error(
          body?.detail ??
            `Agent API returned ${response.status}`,
        );
      }

      setAnalysis(
        body as AgentAnalysis,
      );
    } catch (err) {
      setAnalysisError(
        err instanceof Error
          ? err.message
          : "AI analysis failed.",
      );
    } finally {
      setAnalyzing(false);
    }
  }

  const filteredTickets =
    useMemo(() => {
      const value = search
        .trim()
        .toLowerCase();

      if (!value) {
        return tickets;
      }

      return tickets.filter(
        (ticket) =>
          [
            ticket.subject,
            ticket.description,
            ticket.requester_email,
            ticket.status,
            ticket.priority,
            ticket.category,
            ticket.assigned_team,
            ticket.external_id,
          ]
            .filter(Boolean)
            .some((field) =>
              String(field)
                .toLowerCase()
                .includes(value),
            ),
      );
    }, [tickets, search]);

  return (
    <div className="min-h-screen bg-[#070b14] text-white">
      <div className="flex min-h-screen">
        <aside className="hidden w-64 shrink-0 border-r border-slate-800 bg-[#090e18] p-5 lg:block">
          <div className="mb-8 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-cyan-400 text-slate-950">
              <BrainCircuit className="h-6 w-6" />
            </div>

            <div>
              <h1 className="font-semibold tracking-tight">
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
              icon={TicketIcon}
              label="Tickets"
              active
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

              <span className="font-medium">
                Backend connected
              </span>
            </div>

            <p className="mt-2 text-xs leading-5 text-slate-500">
              FastAPI + PostgreSQL +
              pgvector
            </p>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="border-b border-slate-800 bg-[#090e18]/80 px-6 py-5 backdrop-blur xl:px-10">
            <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
              <div>
                <p className="text-sm font-medium text-cyan-400">
                  Customer Operations
                </p>

                <h2 className="mt-1 text-2xl font-semibold tracking-tight">
                  Tickets Workspace
                </h2>

                <p className="mt-1 text-sm text-slate-500">
                  Review support cases and
                  run live CXOps AI agent
                  workflows.
                </p>
              </div>

              <button
                onClick={() =>
                  void loadTickets()
                }
                disabled={loading}
                className="flex w-fit items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2 text-sm font-medium transition hover:bg-slate-800 disabled:opacity-50"
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
              <div className="mb-6 flex items-center gap-3 rounded-xl border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">
                <TriangleAlert className="h-5 w-5" />
                {error}
              </div>
            )}

            <section className="mb-6 grid gap-4 md:grid-cols-3">
              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <p className="text-sm text-slate-500">
                  Total tickets
                </p>

                <p className="mt-2 text-3xl font-semibold">
                  {tickets.length}
                </p>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <p className="text-sm text-slate-500">
                  Open / pending
                </p>

                <p className="mt-2 text-3xl font-semibold text-amber-300">
                  {
                    tickets.filter(
                      (ticket) =>
                        [
                          "open",
                          "pending",
                          "new",
                        ].includes(
                          ticket.status.toLowerCase(),
                        ),
                    ).length
                  }
                </p>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <p className="text-sm text-slate-500">
                  High / urgent priority
                </p>

                <p className="mt-2 text-3xl font-semibold text-red-300">
                  {
                    tickets.filter(
                      (ticket) =>
                        [
                          "high",
                          "urgent",
                        ].includes(
                          ticket.priority?.toLowerCase() ??
                            "",
                        ),
                    ).length
                  }
                </p>
              </div>
            </section>

            <div className="grid gap-6 xl:grid-cols-[420px_minmax(0,1fr)]">
              <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/60">
                <div className="border-b border-slate-800 p-4">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />

                    <input
                      value={search}
                      onChange={(event) =>
                        setSearch(
                          event.target.value,
                        )
                      }
                      placeholder="Search tickets..."
                      className="w-full rounded-xl border border-slate-700 bg-slate-950 py-2.5 pl-10 pr-4 text-sm outline-none transition placeholder:text-slate-600 focus:border-cyan-500"
                    />
                  </div>
                </div>

                <div className="max-h-[760px] overflow-y-auto">
                  {loading &&
                  tickets.length === 0 ? (
                    <div className="flex h-48 items-center justify-center">
                      <RefreshCw className="h-5 w-5 animate-spin text-cyan-400" />
                    </div>
                  ) : filteredTickets.length ===
                    0 ? (
                    <div className="p-8 text-center text-sm text-slate-500">
                      No tickets found.
                    </div>
                  ) : (
                    filteredTickets.map(
                      (ticket) => (
                        <button
                          key={ticket.id}
                          onClick={() =>
                            selectTicket(
                              ticket,
                            )
                          }
                          className={`w-full border-b border-slate-800 p-4 text-left transition last:border-b-0 ${
                            selectedTicket?.id ===
                            ticket.id
                              ? "bg-cyan-400/10"
                              : "hover:bg-slate-800/50"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-slate-500">
                                  #
                                  {
                                    ticket.id
                                  }
                                </span>

                                {ticket.external_id && (
                                  <span className="text-xs text-slate-600">
                                    Zendesk #
                                    {
                                      ticket.external_id
                                    }
                                  </span>
                                )}
                              </div>

                              <p className="mt-1 truncate text-sm font-medium text-slate-200">
                                {
                                  ticket.subject
                                }
                              </p>

                              <p className="mt-1 truncate text-xs text-slate-500">
                                {
                                  ticket.requester_email
                                }
                              </p>
                            </div>

                            <Badge
                              variant={priorityVariant(
                                ticket.priority,
                              )}
                            >
                              {ticket.priority ??
                                "none"}
                            </Badge>
                          </div>
                        </button>
                      ),
                    )
                  )}
                </div>
              </section>

              <section className="min-w-0">
                {!selectedTicket ? (
                  <div className="flex min-h-[500px] items-center justify-center rounded-2xl border border-slate-800 bg-slate-900/60">
                    <div className="text-center">
                      <TicketIcon className="mx-auto h-8 w-8 text-slate-600" />

                      <p className="mt-3 text-sm text-slate-500">
                        Select a ticket.
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-6">
                    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-start">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm text-slate-500">
                              Ticket #
                              {
                                selectedTicket.id
                              }
                            </span>

                            <Badge>
                              {
                                selectedTicket.status
                              }
                            </Badge>

                            <Badge
                              variant={priorityVariant(
                                selectedTicket.priority,
                              )}
                            >
                              {selectedTicket.priority ??
                                "no priority"}
                            </Badge>
                          </div>

                          <h3 className="mt-4 text-xl font-semibold">
                            {
                              selectedTicket.subject
                            }
                          </h3>
                        </div>

                        <button
                          onClick={() =>
                            void analyzeTicket()
                          }
                          disabled={analyzing}
                          className="flex shrink-0 items-center justify-center gap-2 rounded-xl bg-cyan-400 px-5 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {analyzing ? (
                            <LoaderCircle className="h-4 w-4 animate-spin" />
                          ) : (
                            <Bot className="h-4 w-4" />
                          )}

                          {analyzing
                            ? "Analyzing..."
                            : "Analyze with AI"}
                        </button>
                      </div>

                      <div className="mt-6 rounded-xl border border-slate-800 bg-slate-950/60 p-5">
                        <p className="whitespace-pre-wrap text-sm leading-7 text-slate-300">
                          {
                            selectedTicket.description
                          }
                        </p>
                      </div>
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                        <div className="flex items-center gap-2">
                          <User className="h-4 w-4 text-cyan-400" />

                          <h4 className="font-medium">
                            Customer
                          </h4>
                        </div>

                        <div className="mt-4 space-y-3 text-sm">
                          <div>
                            <p className="text-xs text-slate-500">
                              Email
                            </p>

                            <p className="mt-1 text-slate-300">
                              {
                                selectedTicket.requester_email
                              }
                            </p>
                          </div>

                          <div>
                            <p className="text-xs text-slate-500">
                              Customer ID
                            </p>

                            <p className="mt-1 text-slate-300">
                              {selectedTicket.customer_id ??
                                "—"}
                            </p>
                          </div>

                          <div>
                            <p className="text-xs text-slate-500">
                              Source
                            </p>

                            <p className="mt-1 text-slate-300">
                              {
                                selectedTicket.source
                              }
                            </p>
                          </div>
                        </div>
                      </div>

                      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                        <div className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-cyan-400" />

                          <h4 className="font-medium">
                            Routing
                          </h4>
                        </div>

                        <div className="mt-4 space-y-3 text-sm">
                          <div>
                            <p className="text-xs text-slate-500">
                              Category
                            </p>

                            <p className="mt-1 text-slate-300">
                              {selectedTicket.category ??
                                "Unclassified"}
                            </p>
                          </div>

                          <div>
                            <p className="text-xs text-slate-500">
                              Assigned team
                            </p>

                            <p className="mt-1 text-slate-300">
                              {selectedTicket.assigned_team ??
                                "Unassigned"}
                            </p>
                          </div>

                          <div>
                            <p className="text-xs text-slate-500">
                              Last updated
                            </p>

                            <p className="mt-1 text-slate-300">
                              {new Date(
                                selectedTicket.updated_at,
                              ).toLocaleString()}
                            </p>
                          </div>
                        </div>
                      </div>
                    </div>

                    {analysisError && (
                      <div className="flex items-center gap-3 rounded-2xl border border-red-900/50 bg-red-950/20 p-5 text-sm text-red-300">
                        <XCircle className="h-5 w-5 shrink-0" />

                        {analysisError}
                      </div>
                    )}

                    {analyzing && (
                      <div className="rounded-2xl border border-cyan-900/50 bg-cyan-950/10 p-8">
                        <div className="flex flex-col items-center justify-center text-center">
                          <LoaderCircle className="h-8 w-8 animate-spin text-cyan-400" />

                          <p className="mt-4 font-medium text-cyan-200">
                            CXOps Agent is
                            analyzing the ticket
                          </p>

                          <p className="mt-2 max-w-lg text-sm leading-6 text-slate-500">
                            Evaluating policy
                            requirements, retrieving
                            relevant knowledge and
                            building an authorized
                            tool plan.
                          </p>
                        </div>
                      </div>
                    )}

                    {!analysis &&
                      !analyzing &&
                      !analysisError && (
                        <div className="rounded-2xl border border-dashed border-cyan-900 bg-cyan-950/10 p-6">
                          <div className="flex gap-3">
                            <Bot className="mt-0.5 h-5 w-5 shrink-0 text-cyan-400" />

                            <div>
                              <h4 className="font-medium text-cyan-200">
                                AI Analysis
                              </h4>

                              <p className="mt-2 text-sm leading-6 text-slate-500">
                                Click{" "}
                                <strong className="text-slate-300">
                                  Analyze with AI
                                </strong>{" "}
                                to execute the live
                                CXOps LangGraph
                                workflow.
                              </p>
                            </div>
                          </div>
                        </div>
                      )}

                    {analysis && (
                      <>
                        <div className="rounded-2xl border border-cyan-900/50 bg-slate-900/70 p-6">
                          <div className="flex flex-col justify-between gap-4 md:flex-row md:items-start">
                            <div>
                              <div className="flex items-center gap-2">
                                <Sparkles className="h-5 w-5 text-cyan-400" />

                                <h3 className="font-semibold">
                                  AI Agent Decision
                                </h3>
                              </div>

                              <p className="mt-1 text-xs text-slate-500">
                                Run{" "}
                                <span className="font-mono">
                                  {
                                    analysis.run_id
                                  }
                                </span>
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
                                {formatAction(
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
                                  required
                                </Badge>
                              ) : (
                                <Badge variant="success">
                                  No approval
                                  required
                                </Badge>
                              )}
                            </div>
                          </div>

                          <div className="mt-6 rounded-xl border border-slate-800 bg-slate-950/60 p-5">
                            <p className="text-xs font-medium uppercase tracking-wider text-slate-500">
                              Reason
                            </p>

                            <p className="mt-2 text-sm leading-7 text-slate-300">
                              {
                                analysis
                                  .decision
                                  .reason
                              }
                            </p>
                          </div>

                          <div className="mt-4 grid gap-4 md:grid-cols-2">
                            <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
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

                            <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
                              <p className="text-xs text-slate-500">
                                Recommended
                                priority
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
                            <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/40 p-5">
                              <p className="text-xs font-medium uppercase tracking-wider text-slate-500">
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
                            <div className="mt-4 flex items-start gap-3 rounded-xl border border-emerald-900/40 bg-emerald-950/20 p-4">
                              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" />

                              <div>
                                <p className="text-sm font-medium text-emerald-300">
                                  Automatically
                                  approved and
                                  queued
                                </p>

                                <p className="mt-1 text-xs text-slate-500">
                                  Durable worker
                                  job #
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
                            <div className="mt-4 flex items-start gap-3 rounded-xl border border-amber-900/40 bg-amber-950/20 p-4">
                              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />

                              <div>
                                <p className="text-sm font-medium text-amber-300">
                                  Human-in-the-loop
                                  checkpoint
                                </p>

                                <p className="mt-1 text-xs leading-5 text-slate-500">
                                  This action will
                                  not be executed
                                  until an authorized
                                  reviewer approves
                                  the agent run.
                                </p>
                              </div>
                            </div>
                          )}
                        </div>

                        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                          <div className="flex items-center gap-2">
                            <Workflow className="h-5 w-5 text-cyan-400" />

                            <h3 className="font-semibold">
                              LangGraph Workflow
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
                                    {formatWorkflowStep(
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
                        </div>

                        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                          <div className="flex items-center justify-between gap-4">
                            <div>
                              <div className="flex items-center gap-2">
                                <BrainCircuit className="h-5 w-5 text-cyan-400" />

                                <h3 className="font-semibold">
                                  RAG Knowledge
                                </h3>
                              </div>

                              <p className="mt-1 text-sm text-slate-500">
                                Retrieved policy
                                evidence used for
                                the decision.
                              </p>
                            </div>

                            <Badge variant="info">
                              {
                                analysis
                                  .sources
                                  .length
                              }{" "}
                              source
                              {analysis
                                .sources
                                .length === 1
                                ? ""
                                : "s"}
                            </Badge>
                          </div>

                          {analysis.sources
                            .length === 0 ? (
                            <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-5 text-sm text-slate-500">
                              No knowledge
                              retrieval was
                              required for this
                              decision.
                            </div>
                          ) : (
                            <div className="mt-5 space-y-4">
                              {analysis.sources.map(
                                (source) => (
                                  <div
                                    key={
                                      source.source_id
                                    }
                                    className="rounded-xl border border-slate-800 bg-slate-950/50 p-5"
                                  >
                                    <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                                      <div>
                                        <p className="text-sm font-medium text-slate-200">
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

                                      <Badge variant="info">
                                        {(
                                          source.similarity *
                                          100
                                        ).toFixed(
                                          1,
                                        )}
                                        % match
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
                        </div>

                        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                          <div className="flex items-center justify-between gap-4">
                            <div>
                              <div className="flex items-center gap-2">
                                <ShieldCheck className="h-5 w-5 text-cyan-400" />

                                <h3 className="font-semibold">
                                  Tool
                                  Authorization
                                  Plan
                                </h3>
                              </div>

                              <p className="mt-1 text-sm text-slate-500">
                                Explicit tools and
                                risk controls chosen
                                by CXOps AI.
                              </p>
                            </div>

                            <Badge>
                              {
                                analysis
                                  .tool_plan
                                  .length
                              }{" "}
                              tool
                              {analysis
                                .tool_plan
                                .length === 1
                                ? ""
                                : "s"}
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
                                    <div>
                                      <p className="font-mono text-sm text-cyan-300">
                                        {
                                          tool.tool
                                        }
                                      </p>

                                      <p className="mt-1 text-xs text-slate-600">
                                        Tool{" "}
                                        {index +
                                          1}
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
                                          : "Awaiting authorization"}
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
                                    <div className="mt-4 rounded-lg bg-slate-950 p-4">
                                      <p className="mb-2 text-xs uppercase tracking-wider text-slate-600">
                                        Arguments
                                      </p>

                                      <pre className="overflow-x-auto text-xs leading-6 text-slate-400">
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
                        </div>
                      </>
                    )}
                  </div>
                )}
              </section>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}