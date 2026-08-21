"use client";

import {
  Bot,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  LoaderCircle,
  Mail,
  Plus,
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

import AppSidebar from "@/components/app-sidebar";

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

function priorityVariant(
  priority: string | null,
): BadgeVariant {
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
  }
}

function riskVariant(
  risk: ToolPlanItem["risk_level"],
): BadgeVariant {
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

function StatCard({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: number;
  note: string;
  tone:
    | "violet"
    | "amber"
    | "rose";
}) {
  const toneClass = {
    violet:
      "from-violet-500/10 to-indigo-500/[0.025] text-violet-600",
    amber:
      "from-amber-400/12 to-orange-400/[0.025] text-amber-600",
    rose:
      "from-rose-400/12 to-red-400/[0.025] text-rose-600",
  }[tone];

  return (
    <div
      className={`app-panel relative overflow-hidden rounded-[20px] bg-gradient-to-br p-6 ${toneClass}`}
    >
      <div className="absolute -right-8 -top-8 h-24 w-24 rounded-full bg-current opacity-[0.04] blur-xl" />

      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
        {label}
      </p>

      <p className="editorial-number mt-4 text-4xl font-medium tracking-[-0.045em] text-slate-950">
        {value}
      </p>

      <p className="mt-3 text-xs leading-5 text-slate-500">
        {note}
      </p>
    </div>
  );
}

function InfoRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-5 border-b border-slate-200/70 py-3.5 last:border-0">
      <span className="text-xs text-slate-400">
        {label}
      </span>

      <span className="max-w-[68%] text-right text-sm font-medium text-slate-700">
        {value}
      </span>
    </div>
  );
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

        const data = await response.json();

        const rows: Ticket[] =
          Array.isArray(data)
            ? data
            : Array.isArray(data.items)
              ? data.items
              : [];

        setTickets(rows);

        if (rows.length > 0) {
          setSelectedTicket(
            (current) => {
              if (!current) {
                return rows[0];
              }

              return (
                rows.find(
                  (ticket) =>
                    ticket.id ===
                    current.id,
                ) ?? rows[0]
              );
            },
          );
        } else {
          setSelectedTicket(null);
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

  const openTickets =
    useMemo(
      () =>
        tickets.filter((ticket) =>
          [
            "open",
            "pending",
            "new",
          ].includes(
            ticket.status.toLowerCase(),
          ),
        ).length,
      [tickets],
    );

  const priorityTickets =
    useMemo(
      () =>
        tickets.filter((ticket) =>
          ["high", "urgent"].includes(
            ticket.priority?.toLowerCase() ??
              "",
          ),
        ).length,
      [tickets],
    );

  return (
    <div className="min-h-screen">
      <AppSidebar active="/tickets" />

      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Tickets
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Customer operations workspace
              </p>
            </div>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() =>
                  void loadTickets()
                }
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh tickets"
              >
                <RefreshCw
                  className={`h-4 w-4 ${
                    loading
                      ? "animate-spin"
                      : ""
                  }`}
                />
              </button>

              <Link
                href="/tickets/new"
                className="inline-flex items-center gap-2 rounded-full bg-[#111827] px-5 py-2.5 text-xs font-medium text-white shadow-[0_9px_24px_rgba(17,24,39,0.14)] transition hover:-translate-y-0.5"
              >
                <Plus className="h-4 w-4" />
                New ticket
              </Link>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-end">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
                  Customer Operations
                </p>

                <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
                  Tickets workspace
                </h1>

                <p className="mt-3 max-w-xl text-sm leading-7 text-slate-500">
                  Review customer cases,
                  inspect routing context and
                  run live CXOps AI workflows
                  from one workspace.
                </p>
              </div>

              <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white/70 px-4 py-2 text-[11px] text-slate-500 shadow-sm">
                <CircleDot className="h-3.5 w-3.5 text-emerald-500" />
                Live ticket data
              </div>
            </div>
          </section>

          {error && (
            <div className="mb-7 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
              {error}
            </div>
          )}

          <section className="mb-7 grid gap-4 md:grid-cols-3">
            <StatCard
              label="Total tickets"
              value={tickets.length}
              note="All tickets currently stored in CXOps."
              tone="violet"
            />

            <StatCard
              label="Open / pending"
              value={openTickets}
              note="Cases that still require operational attention."
              tone="amber"
            />

            <StatCard
              label="High / urgent"
              value={priorityTickets}
              note="Priority cases that may require faster handling."
              tone="rose"
            />
          </section>

          <div className="grid gap-6 xl:grid-cols-[390px_minmax(0,1fr)]">
            <section className="app-panel self-start overflow-hidden rounded-[22px] xl:sticky xl:top-[96px]">
              <div className="border-b border-slate-200/70 p-4">
                <div className="relative">
                  <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />

                  <input
                    value={search}
                    onChange={(event) =>
                      setSearch(
                        event.target.value,
                      )
                    }
                    placeholder="Search tickets..."
                    className="w-full rounded-xl border border-slate-200 bg-[#fbfcff] py-3 pl-10 pr-4 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                  />
                </div>

                <div className="mt-3 flex justify-between px-1 text-[10px] uppercase tracking-[0.13em] text-slate-400">
                  <span>
                    {filteredTickets.length} results
                  </span>

                  <span>
                    Updated live
                  </span>
                </div>
              </div>

              <div
                data-lenis-prevent
                className="max-h-[760px] overflow-y-auto overscroll-contain"
              >
                {loading &&
                tickets.length === 0 ? (
                  <div className="flex h-52 items-center justify-center">
                    <LoaderCircle className="h-6 w-6 animate-spin text-violet-500" />
                  </div>
                ) : filteredTickets.length ===
                  0 ? (
                  <div className="p-10 text-center">
                    <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-50">
                      <Search className="h-5 w-5 text-slate-400" />
                    </div>

                    <p className="mt-4 text-sm font-medium text-slate-700">
                      No tickets found
                    </p>

                    <p className="mt-1 text-xs text-slate-400">
                      Try another search term.
                    </p>
                  </div>
                ) : (
                  filteredTickets.map(
                    (ticket) => {
                      const selected =
                        selectedTicket?.id ===
                        ticket.id;

                      return (
                        <button
                          key={ticket.id}
                          type="button"
                          onClick={() =>
                            selectTicket(
                              ticket,
                            )
                          }
                          className={`group relative w-full border-b border-slate-200/60 p-4 text-left transition last:border-b-0 ${
                            selected
                              ? "bg-gradient-to-r from-violet-50/90 via-blue-50/50 to-white"
                              : "bg-white/30 hover:bg-slate-50/80"
                          }`}
                        >
                          {selected && (
                            <span className="absolute bottom-3 left-0 top-3 w-[3px] rounded-r-full bg-gradient-to-b from-violet-500 to-blue-500" />
                          )}

                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-slate-400">
                                  #{ticket.id}
                                </span>

                                {ticket.external_id && (
                                  <span className="text-[10px] text-blue-500">
                                    Zendesk #
                                    {
                                      ticket.external_id
                                    }
                                  </span>
                                )}
                              </div>

                              <p
                                className={`mt-2 truncate text-sm font-medium ${
                                  selected
                                    ? "text-slate-950"
                                    : "text-slate-800"
                                }`}
                              >
                                {ticket.subject}
                              </p>

                              <p className="mt-1.5 truncate text-xs text-slate-400">
                                {
                                  ticket.requester_email
                                }
                              </p>

                              <div className="mt-3 flex flex-wrap items-center gap-2">
                                <Badge>
                                  {
                                    ticket.status
                                  }
                                </Badge>

                                <Badge
                                  variant={priorityVariant(
                                    ticket.priority,
                                  )}
                                >
                                  {ticket.priority ??
                                    "none"}
                                </Badge>
                              </div>
                            </div>

                            <ChevronRight
                              className={`mt-1 h-4 w-4 shrink-0 transition ${
                                selected
                                  ? "translate-x-0.5 text-violet-500"
                                  : "text-slate-300 group-hover:translate-x-0.5 group-hover:text-slate-500"
                              }`}
                            />
                          </div>
                        </button>
                      );
                    },
                  )
                )}
              </div>
            </section>

            <section className="min-w-0">
              {!selectedTicket ? (
                <div className="app-panel flex min-h-[540px] items-center justify-center rounded-[22px]">
                  <div className="text-center">
                    <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-50 to-blue-50">
                      <TicketIcon className="h-6 w-6 text-violet-500" />
                    </div>

                    <p className="mt-4 font-medium text-slate-800">
                      Select a ticket
                    </p>

                    <p className="mt-1 text-sm text-slate-400">
                      Ticket details will appear
                      here.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="space-y-6">
                  <div className="app-panel overflow-hidden rounded-[22px]">
                    <div className="relative overflow-hidden p-6 md:p-8">
                      <div className="pointer-events-none absolute -right-28 -top-36 h-80 w-80 rounded-full bg-violet-300/10 blur-3xl" />

                      <div className="relative flex flex-col justify-between gap-6 md:flex-row md:items-start">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                              Ticket #
                              {
                                selectedTicket.id
                              }
                            </span>

                            {selectedTicket.external_id && (
                              <Badge variant="info">
                                Zendesk #
                                {
                                  selectedTicket.external_id
                                }
                              </Badge>
                            )}

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

                          <h2 className="mt-5 max-w-3xl text-2xl font-medium tracking-[-0.035em] text-slate-950 md:text-3xl">
                            {
                              selectedTicket.subject
                            }
                          </h2>
                        </div>

                        <button
                          type="button"
                          onClick={() =>
                            void analyzeTicket()
                          }
                          disabled={analyzing}
                          className="group flex shrink-0 items-center justify-center gap-2.5 rounded-full bg-gradient-to-r from-[#765cff] to-[#508cff] px-5 py-3 text-sm font-medium text-white shadow-[0_12px_30px_rgba(104,86,255,0.22)] transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {analyzing ? (
                            <LoaderCircle className="h-4 w-4 animate-spin" />
                          ) : (
                            <Sparkles className="h-4 w-4" />
                          )}

                          {analyzing
                            ? "Analyzing..."
                            : "Analyze with AI"}
                        </button>
                      </div>

                      <div className="relative mt-7 rounded-2xl border border-slate-200/70 bg-[#fbfcff]/80 p-5 md:p-6">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                          Customer message
                        </p>

                        <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-600">
                          {
                            selectedTicket.description
                          }
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-5 md:grid-cols-2">
                    <div className="app-panel rounded-[20px] p-6">
                      <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                          <User className="h-5 w-5" />
                        </div>

                        <div>
                          <p className="font-medium text-slate-900">
                            Customer
                          </p>

                          <p className="text-xs text-slate-400">
                            Requester context
                          </p>
                        </div>
                      </div>

                      <div className="mt-5">
                        <InfoRow
                          label="Email"
                          value={
                            <span className="inline-flex items-center gap-1.5">
                              <Mail className="h-3.5 w-3.5 text-slate-400" />
                              {
                                selectedTicket.requester_email
                              }
                            </span>
                          }
                        />

                        <InfoRow
                          label="Customer ID"
                          value={
                            selectedTicket.customer_id ??
                            "—"
                          }
                        />

                        <InfoRow
                          label="Source"
                          value={
                            selectedTicket.source
                          }
                        />
                      </div>
                    </div>

                    <div className="app-panel rounded-[20px] p-6">
                      <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
                          <Workflow className="h-5 w-5" />
                        </div>

                        <div>
                          <p className="font-medium text-slate-900">
                            Routing
                          </p>

                          <p className="text-xs text-slate-400">
                            Operational context
                          </p>
                        </div>
                      </div>

                      <div className="mt-5">
                        <InfoRow
                          label="Category"
                          value={
                            selectedTicket.category ??
                            "Unclassified"
                          }
                        />

                        <InfoRow
                          label="Assigned team"
                          value={
                            selectedTicket.assigned_team ??
                            "Unassigned"
                          }
                        />

                        <InfoRow
                          label="Last updated"
                          value={new Date(
                            selectedTicket.updated_at,
                          ).toLocaleString()}
                        />
                      </div>
                    </div>
                  </div>

                  {analysisError && (
                    <div className="flex items-start gap-3 rounded-[20px] border border-red-200 bg-red-50/80 p-5 text-sm text-red-700">
                      <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
                      {analysisError}
                    </div>
                  )}

                  {analyzing && (
                    <div className="app-panel relative overflow-hidden rounded-[22px] p-9">
                      <div className="absolute inset-0 bg-gradient-to-br from-violet-50/80 via-transparent to-blue-50/70" />

                      <div className="relative flex flex-col items-center justify-center text-center">
                        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white shadow-[0_12px_35px_rgba(95,82,255,0.12)]">
                          <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
                        </div>

                        <p className="mt-5 font-medium text-slate-900">
                          CXOps Agent is analyzing
                          the ticket
                        </p>

                        <p className="mt-2 max-w-lg text-sm leading-6 text-slate-500">
                          Evaluating policy
                          requirements, retrieving
                          relevant knowledge and
                          building an authorized
                          tool plan.
                        </p>

                        <div className="mt-6 flex items-center gap-2">
                          {[
                            "Policy",
                            "RAG",
                            "Decision",
                            "Tools",
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
                    !analyzing &&
                    !analysisError && (
                      <div className="rounded-[22px] border border-dashed border-violet-200 bg-gradient-to-br from-violet-50/55 to-blue-50/40 p-6">
                        <div className="flex gap-4">
                          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white text-violet-500 shadow-sm">
                            <Bot className="h-5 w-5" />
                          </div>

                          <div>
                            <h3 className="font-medium text-slate-900">
                              AI Analysis
                            </h3>

                            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
                              Run the live CXOps
                              agent workflow to
                              evaluate the ticket,
                              retrieve relevant
                              knowledge and prepare
                              its authorized tool
                              plan.
                            </p>
                          </div>
                        </div>
                      </div>
                    )}

                  {analysis && (
                    <>
                      <div className="app-panel overflow-hidden rounded-[22px]">
                        <div className="border-b border-slate-200/70 bg-gradient-to-r from-violet-50/70 via-white to-blue-50/60 p-6 md:p-7">
                          <div className="flex flex-col justify-between gap-4 md:flex-row md:items-start">
                            <div>
                              <div className="flex items-center gap-3">
                                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-blue-500 text-white shadow-[0_9px_24px_rgba(104,86,255,0.18)]">
                                  <Sparkles className="h-5 w-5" />
                                </div>

                                <div>
                                  <h3 className="font-medium text-slate-950">
                                    AI Agent Decision
                                  </h3>

                                  <p className="mt-0.5 text-[10px] text-slate-400">
                                    Run{" "}
                                    <span className="font-mono">
                                      {
                                        analysis.run_id
                                      }
                                    </span>
                                  </p>
                                </div>
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
                                {formatAction(
                                  analysis
                                    .decision
                                    .action,
                                )}
                              </Badge>

                              {analysis.decision
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
                        </div>

                        <div className="p-6 md:p-7">
                          <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-5">
                            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                              Reason
                            </p>

                            <p className="mt-3 text-sm leading-7 text-slate-600">
                              {
                                analysis
                                  .decision
                                  .reason
                              }
                            </p>
                          </div>

                          <div className="mt-4 grid gap-4 md:grid-cols-2">
                            <div className="rounded-2xl border border-slate-200/70 bg-white p-5">
                              <p className="text-xs text-slate-400">
                                Recommended team
                              </p>

                              <p className="mt-2 font-medium text-slate-900">
                                {analysis
                                  .decision
                                  .recommended_team ??
                                  "—"}
                              </p>
                            </div>

                            <div className="rounded-2xl border border-slate-200/70 bg-white p-5">
                              <p className="text-xs text-slate-400">
                                Recommended
                                priority
                              </p>

                              <p className="mt-2 font-medium capitalize text-slate-900">
                                {analysis
                                  .decision
                                  .recommended_priority ??
                                  "—"}
                              </p>
                            </div>
                          </div>

                          {analysis.decision
                            .response_draft && (
                            <div className="mt-4 rounded-2xl border border-blue-100 bg-blue-50/40 p-5">
                              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-500">
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
                            <div className="mt-4 flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50/70 p-4">
                              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />

                              <div>
                                <p className="text-sm font-medium text-emerald-800">
                                  Automatically
                                  approved and queued
                                </p>

                                <p className="mt-1 text-xs text-emerald-600">
                                  Durable worker job
                                  #
                                  {
                                    analysis.job_id
                                  }
                                </p>
                              </div>
                            </div>
                          )}

                          {analysis.decision
                            .requires_human_approval && (
                            <div className="mt-4 flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
                              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />

                              <div>
                                <p className="text-sm font-medium text-amber-800">
                                  Human-in-the-loop
                                  checkpoint
                                </p>

                                <p className="mt-1 text-xs leading-5 text-amber-700">
                                  This action will not
                                  be executed until an
                                  authorized reviewer
                                  approves the agent
                                  run.
                                </p>
                              </div>
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="app-panel rounded-[22px] p-6 md:p-7">
                        <div className="flex items-center gap-3">
                          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                            <Workflow className="h-5 w-5" />
                          </div>

                          <div>
                            <h3 className="font-medium text-slate-900">
                              LangGraph Workflow
                            </h3>

                            <p className="text-xs text-slate-400">
                              Decision execution path
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
                                <span className="rounded-xl border border-violet-100 bg-gradient-to-br from-white to-violet-50/60 px-3.5 py-2 text-xs font-medium text-slate-700 shadow-sm">
                                  {formatWorkflowStep(
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
                      </div>

                      <div className="app-panel rounded-[22px] p-6 md:p-7">
                        <div className="flex items-start justify-between gap-4">
                          <div>
                            <div className="flex items-center gap-3">
                              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                                <BrainCircuit className="h-5 w-5" />
                              </div>

                              <div>
                                <h3 className="font-medium text-slate-900">
                                  RAG Knowledge
                                </h3>

                                <p className="text-xs text-slate-400">
                                  Retrieved evidence
                                  used for this
                                  decision
                                </p>
                              </div>
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
                          <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50/70 p-5 text-sm text-slate-500">
                            No knowledge retrieval
                            was required for this
                            decision.
                          </div>
                        ) : (
                          <div className="mt-6 space-y-4">
                            {analysis.sources.map(
                              (source) => (
                                <div
                                  key={
                                    source.source_id
                                  }
                                  className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5"
                                >
                                  <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                                    <div>
                                      <p className="text-sm font-medium text-slate-800">
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
                      </div>

                      <div className="app-panel rounded-[22px] p-6 md:p-7">
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex items-center gap-3">
                            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
                              <ShieldCheck className="h-5 w-5" />
                            </div>

                            <div>
                              <h3 className="font-medium text-slate-900">
                                Tool Authorization
                                Plan
                              </h3>

                              <p className="text-xs text-slate-400">
                                Explicit tool and
                                risk controls chosen
                                by CXOps AI
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
                      </div>
                    </>
                  )}
                </div>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}
