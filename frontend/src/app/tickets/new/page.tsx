"use client";

import {
  Activity,
  ArrowLeft,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Gauge,
  LoaderCircle,
  Plus,
  RotateCcw,
  Send,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Ticket,
  TriangleAlert,
  Workflow,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { FormEvent, useState } from "react";

type Priority =
  | "low"
  | "normal"
  | "high"
  | "urgent";

type CreatedTicket = {
  id: number;
  external_id: string | null;
  subject: string;
  description: string;
  status: string;
  priority: string;
  requester_email: string | null;
  source: string;
  created_at: string;
  updated_at: string;
  category: string | null;
  assigned_team: string | null;
  customer_id: number | null;
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

export default function NewTicketPage() {
  const [subject, setSubject] =
    useState("");

  const [description, setDescription] =
    useState("");

  const [email, setEmail] =
    useState("");

  const [priority, setPriority] =
    useState<Priority>("normal");

  const [loading, setLoading] =
    useState(false);

  const [mode, setMode] = useState<
    "create" | "analyze" | null
  >(null);

  const [error, setError] =
    useState("");

  const [createdTicket, setCreatedTicket] =
    useState<CreatedTicket | null>(null);

  const [analysis, setAnalysis] =
    useState<AgentAnalysis | null>(null);

  function resetForm() {
    setSubject("");
    setDescription("");
    setEmail("");
    setPriority("normal");
    setCreatedTicket(null);
    setAnalysis(null);
    setError("");
    setMode(null);
  }

  async function createTicket(
    analyzeAfterCreation: boolean,
  ) {
    if (
      !subject.trim() ||
      !description.trim()
    ) {
      setError(
        "Subject and customer message are required.",
      );

      return;
    }

    setLoading(true);
    setMode(
      analyzeAfterCreation
        ? "analyze"
        : "create",
    );

    setError("");
    setCreatedTicket(null);
    setAnalysis(null);

    let created:
      | CreatedTicket
      | null = null;

    try {
      const response = await fetch(
        "/api/backend/tickets",
        {
          method: "POST",

          headers: {
            "content-type":
              "application/json",
          },

          body: JSON.stringify({
            subject: subject.trim(),

            description:
              description.trim(),

            requester_email:
              email.trim() || null,

            priority,

            source:
              "control-center-test",

            external_id: null,

            customer_id: null,
          }),
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        throw new Error(
          body?.detail
            ? typeof body.detail ===
              "string"
              ? body.detail
              : JSON.stringify(
                  body.detail,
                )
            : `Ticket API returned ${response.status}`,
        );
      }

      created =
        body as CreatedTicket;

      setCreatedTicket(created);

      if (!analyzeAfterCreation) {
        return;
      }

      const agentResponse =
        await fetch(
          `/api/backend/agent/tickets/${created.id}/analyze`,
          {
            method: "POST",
            cache: "no-store",
          },
        );

      const agentBody =
        await agentResponse.json();

      if (!agentResponse.ok) {
        throw new Error(
          agentBody?.detail ??
            `Ticket #${created.id} was created, but AI analysis failed.`,
        );
      }

      setAnalysis(
        agentBody as AgentAnalysis,
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : created
            ? `Ticket #${created.id} was created, but processing failed.`
            : "Ticket creation failed.",
      );
    } finally {
      setLoading(false);
      setMode(null);
    }
  }

  async function handleSubmit(
    event: FormEvent,
  ) {
    event.preventDefault();

    await createTicket(false);
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

              <span>
                Demo mode ready
              </span>
            </div>

            <p className="mt-2 text-xs leading-5 text-slate-500">
              Create custom support cases
              and run them through CXOps AI.
            </p>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="border-b border-slate-800 bg-[#090e18]/80 px-6 py-5 xl:px-10">
            <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
              <div>
                <p className="text-sm font-medium text-cyan-400">
                  Live Demo
                </p>

                <h2 className="mt-1 text-2xl font-semibold">
                  New Test Ticket
                </h2>

                <p className="mt-1 text-sm text-slate-500">
                  Create a completely new
                  customer case and test the
                  CXOps agent against it.
                </p>
              </div>

              <Link
                href="/tickets"
                className="flex w-fit items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2 text-sm transition hover:bg-slate-800"
              >
                <ArrowLeft className="h-4 w-4" />

                Tickets
              </Link>
            </div>
          </header>

          <div className="p-6 xl:p-10">
            {error && (
              <div className="mb-6 flex items-start gap-3 rounded-xl border border-red-900/50 bg-red-950/20 p-4 text-sm text-red-300">
                <XCircle className="mt-0.5 h-5 w-5 shrink-0" />

                <div>
                  <p>{error}</p>

                  {createdTicket && (
                    <p className="mt-1 text-xs text-red-400/70">
                      Ticket #
                      {createdTicket.id} exists
                      in CXOps.
                    </p>
                  )}
                </div>
              </div>
            )}

            <div className="grid gap-6 xl:grid-cols-[480px_minmax(0,1fr)]">
              <section>
                <form
                  onSubmit={handleSubmit}
                  className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6"
                >
                  <div className="flex items-center gap-2">
                    <Plus className="h-5 w-5 text-cyan-400" />

                    <h3 className="font-semibold">
                      Customer Case
                    </h3>
                  </div>

                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    Enter any support request you
                    want to use for the live AI
                    demonstration.
                  </p>

                  <div className="mt-6">
                    <label className="text-xs font-medium text-slate-400">
                      Subject *
                    </label>

                    <input
                      value={subject}
                      onChange={(event) =>
                        setSubject(
                          event.target.value,
                        )
                      }
                      maxLength={255}
                      placeholder="Example: Withdrawal pending for three days"
                      className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none transition placeholder:text-slate-600 focus:border-cyan-500"
                    />

                    <div className="mt-1 text-right text-[11px] text-slate-600">
                      {subject.length}/255
                    </div>
                  </div>

                  <div className="mt-4">
                    <label className="text-xs font-medium text-slate-400">
                      Customer message *
                    </label>

                    <textarea
                      value={description}
                      onChange={(event) =>
                        setDescription(
                          event.target.value,
                        )
                      }
                      rows={9}
                      placeholder="Describe the customer's issue..."
                      className="mt-2 w-full resize-none rounded-xl border border-slate-700 bg-slate-950 p-4 text-sm leading-6 outline-none transition placeholder:text-slate-600 focus:border-cyan-500"
                    />
                  </div>

                  <div className="mt-4">
                    <label className="text-xs font-medium text-slate-400">
                      Requester email
                    </label>

                    <input
                      type="email"
                      value={email}
                      onChange={(event) =>
                        setEmail(
                          event.target.value,
                        )
                      }
                      placeholder="tester@example.com"
                      className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none transition placeholder:text-slate-600 focus:border-cyan-500"
                    />

                    <p className="mt-2 text-xs text-slate-600">
                      Optional for local test
                      tickets.
                    </p>
                  </div>

                  <div className="mt-4">
                    <label className="text-xs font-medium text-slate-400">
                      Priority
                    </label>

                    <select
                      value={priority}
                      onChange={(event) =>
                        setPriority(
                          event.target
                            .value as Priority,
                        )
                      }
                      className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none focus:border-cyan-500"
                    >
                      <option value="low">
                        Low
                      </option>

                      <option value="normal">
                        Normal
                      </option>

                      <option value="high">
                        High
                      </option>

                      <option value="urgent">
                        Urgent
                      </option>
                    </select>
                  </div>

                  <div className="mt-6 grid gap-3">
                    <button
                      type="button"
                      onClick={() =>
                        void createTicket(true)
                      }
                      disabled={
                        loading ||
                        !subject.trim() ||
                        !description.trim()
                      }
                      className="flex items-center justify-center gap-2 rounded-xl bg-cyan-400 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {loading &&
                      mode === "analyze" ? (
                        <LoaderCircle className="h-4 w-4 animate-spin" />
                      ) : (
                        <Sparkles className="h-4 w-4" />
                      )}

                      {loading &&
                      mode === "analyze"
                        ? "Creating & analyzing..."
                        : "Create & Analyze with AI"}
                    </button>

                    <button
                      type="submit"
                      disabled={
                        loading ||
                        !subject.trim() ||
                        !description.trim()
                      }
                      className="flex items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-950 px-5 py-3 text-sm font-medium transition hover:bg-slate-800 disabled:opacity-50"
                    >
                      {loading &&
                      mode === "create" ? (
                        <LoaderCircle className="h-4 w-4 animate-spin" />
                      ) : (
                        <Plus className="h-4 w-4" />
                      )}

                      {loading &&
                      mode === "create"
                        ? "Creating ticket..."
                        : "Create Ticket Only"}
                    </button>
                  </div>
                </form>

                <div className="mt-5 rounded-xl border border-amber-900/30 bg-amber-950/10 p-4">
                  <div className="flex gap-3">
                    <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />

                    <div>
                      <p className="text-sm font-medium text-amber-300">
                        Test ticket
                      </p>

                      <p className="mt-1 text-xs leading-5 text-slate-500">
                        The ticket is stored in the
                        real CXOps PostgreSQL
                        database. It is created
                        without a Zendesk external
                        ID, so this page is intended
                        primarily for testing the AI
                        decision pipeline safely.
                      </p>
                    </div>
                  </div>
                </div>
              </section>

              <section className="min-w-0">
                {!createdTicket &&
                  !loading && (
                    <div className="flex min-h-[620px] items-center justify-center rounded-2xl border border-dashed border-slate-800 bg-slate-900/30">
                      <div className="max-w-lg text-center">
                        <Bot className="mx-auto h-11 w-11 text-slate-600" />

                        <h3 className="mt-4 font-semibold text-slate-300">
                          Live Tester Workspace
                        </h3>

                        <p className="mt-2 text-sm leading-6 text-slate-500">
                          Write any new customer
                          issue. CXOps can store it
                          as a real ticket and
                          immediately process it
                          through the same agent
                          workflow used elsewhere
                          in the application.
                        </p>

                        <div className="mt-6 flex flex-wrap justify-center gap-2">
                          <Badge>
                            Custom input
                          </Badge>

                          <Badge>
                            PostgreSQL
                          </Badge>

                          <Badge>
                            LangGraph
                          </Badge>

                          <Badge>
                            RAG
                          </Badge>

                          <Badge>
                            Risk controls
                          </Badge>
                        </div>
                      </div>
                    </div>
                  )}

                {loading &&
                  !createdTicket && (
                    <div className="flex min-h-[620px] items-center justify-center rounded-2xl border border-cyan-900/40 bg-cyan-950/10">
                      <div className="text-center">
                        <LoaderCircle className="mx-auto h-9 w-9 animate-spin text-cyan-400" />

                        <p className="mt-4 font-medium">
                          {mode === "analyze"
                            ? "Creating ticket..."
                            : "Saving ticket..."}
                        </p>
                      </div>
                    </div>
                  )}

                {createdTicket && (
                  <div className="space-y-6">
                    <section className="rounded-2xl border border-emerald-900/40 bg-slate-900/60 p-6">
                      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                        <div>
                          <div className="flex items-center gap-2">
                            <CheckCircle2 className="h-5 w-5 text-emerald-400" />

                            <h3 className="font-semibold">
                              Ticket Created
                            </h3>
                          </div>

                          <p className="mt-2 text-xs text-slate-500">
                            CXOps Ticket #
                            {createdTicket.id}
                          </p>
                        </div>

                        <div className="flex flex-wrap gap-2">
                          <Badge variant="success">
                            {
                              createdTicket.status
                            }
                          </Badge>

                          <Badge
                            variant={
                              createdTicket.priority ===
                                "high" ||
                              createdTicket.priority ===
                                "urgent"
                                ? "danger"
                                : "warning"
                            }
                          >
                            {
                              createdTicket.priority
                            }
                          </Badge>
                        </div>
                      </div>

                      <h4 className="mt-5 text-xl font-semibold">
                        {
                          createdTicket.subject
                        }
                      </h4>

                      <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/50 p-5">
                        <p className="whitespace-pre-wrap text-sm leading-7 text-slate-300">
                          {
                            createdTicket.description
                          }
                        </p>
                      </div>

                      <div className="mt-4 grid gap-3 sm:grid-cols-3">
                        <div className="rounded-xl bg-slate-950/50 p-4">
                          <p className="text-xs text-slate-600">
                            Source
                          </p>

                          <p className="mt-2 text-sm">
                            {
                              createdTicket.source
                            }
                          </p>
                        </div>

                        <div className="rounded-xl bg-slate-950/50 p-4">
                          <p className="text-xs text-slate-600">
                            Category
                          </p>

                          <p className="mt-2 text-sm">
                            {createdTicket.category ??
                              "Unclassified"}
                          </p>
                        </div>

                        <div className="rounded-xl bg-slate-950/50 p-4">
                          <p className="text-xs text-slate-600">
                            Assigned team
                          </p>

                          <p className="mt-2 text-sm">
                            {createdTicket.assigned_team ??
                              "Unassigned"}
                          </p>
                        </div>
                      </div>

                      <div className="mt-5 flex flex-wrap gap-3">
                        <Link
                          href="/tickets"
                          className="rounded-xl border border-slate-700 bg-slate-950 px-4 py-2 text-sm transition hover:bg-slate-800"
                        >
                          Open Tickets
                        </Link>

                        <Link
                          href="/agent"
                          className="rounded-xl border border-slate-700 bg-slate-950 px-4 py-2 text-sm transition hover:bg-slate-800"
                        >
                          AI Agent
                        </Link>

                        <button
                          onClick={resetForm}
                          className="flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-950 px-4 py-2 text-sm transition hover:bg-slate-800"
                        >
                          <RotateCcw className="h-4 w-4" />

                          Create Another
                        </button>
                      </div>
                    </section>

                    {loading &&
                      mode === "analyze" &&
                      !analysis && (
                        <section className="flex min-h-[260px] items-center justify-center rounded-2xl border border-cyan-900/40 bg-cyan-950/10">
                          <div className="text-center">
                            <LoaderCircle className="mx-auto h-8 w-8 animate-spin text-cyan-400" />

                            <p className="mt-4 font-medium">
                              CXOps Agent is
                              analyzing Ticket #
                              {createdTicket.id}
                            </p>

                            <p className="mt-2 text-sm text-slate-500">
                              Knowledge gate →
                              RAG → decision →
                              authorization
                            </p>
                          </div>
                        </section>
                      )}

                    {!analysis &&
                      !loading && (
                        <section className="rounded-2xl border border-dashed border-cyan-900/50 bg-cyan-950/10 p-6">
                          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                            <div>
                              <div className="flex items-center gap-2">
                                <Bot className="h-5 w-5 text-cyan-400" />

                                <p className="font-medium">
                                  Ticket ready for AI
                                </p>
                              </div>

                              <p className="mt-2 text-sm text-slate-500">
                                Run the new ticket
                                through CXOps AI now.
                              </p>
                            </div>

                            <button
                              onClick={() =>
                                void createTicket(
                                  true,
                                )
                              }
                              className="flex items-center justify-center gap-2 rounded-xl bg-cyan-400 px-5 py-2.5 text-sm font-semibold text-slate-950"
                            >
                              <Send className="h-4 w-4" />

                              Analyze
                            </button>
                          </div>
                        </section>
                      )}

                    {analysis && (
                      <>
                        <section className="rounded-2xl border border-cyan-900/40 bg-slate-900/60 p-6">
                          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                            <div>
                              <div className="flex items-center gap-2">
                                <Sparkles className="h-5 w-5 text-cyan-400" />

                                <h3 className="font-semibold">
                                  CXOps AI Decision
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
                                  No approval
                                </Badge>
                              )}
                            </div>
                          </div>

                          <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-5">
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
                              <p className="text-xs text-slate-600">
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
                              <p className="text-xs text-slate-600">
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
                            <div className="mt-4 rounded-xl border border-cyan-900/40 bg-cyan-950/10 p-5">
                              <p className="text-xs uppercase tracking-wider text-cyan-500">
                                Proposed response
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

                          {analysis
                            .decision
                            .requires_human_approval && (
                            <div className="mt-4 flex gap-3 rounded-xl border border-amber-900/40 bg-amber-950/20 p-4">
                              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />

                              <div>
                                <p className="text-sm font-medium text-amber-300">
                                  Human-in-the-loop
                                  control activated
                                </p>

                                <p className="mt-1 text-xs leading-5 text-slate-500">
                                  Sensitive actions
                                  remain blocked until
                                  reviewed in the
                                  Approval Queue.
                                </p>
                              </div>
                            </div>
                          )}

                          {analysis.auto_queued && (
                            <div className="mt-4 flex gap-3 rounded-xl border border-emerald-900/40 bg-emerald-950/20 p-4">
                              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" />

                              <div>
                                <p className="text-sm font-medium text-emerald-300">
                                  Autonomous action
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
                        </section>

                        <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                          <div className="flex items-center gap-2">
                            <Workflow className="h-5 w-5 text-cyan-400" />

                            <h3 className="font-semibold">
                              LangGraph Path
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
                            <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-5 text-sm text-slate-500">
                              No knowledge retrieval
                              was required.
                            </div>
                          ) : (
                            <div className="mt-5 space-y-4">
                              {analysis.sources.map(
                                (source) => (
                                  <div
                                    key={`${source.source_id}-${source.chunk_id}`}
                                    className="rounded-xl border border-slate-800 bg-slate-950/50 p-5"
                                  >
                                    <div className="flex flex-col justify-between gap-3 sm:flex-row">
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
                          <div className="flex items-center justify-between gap-4">
                            <div className="flex items-center gap-2">
                              <ShieldCheck className="h-5 w-5 text-cyan-400" />

                              <h3 className="font-semibold">
                                Tool Authorization
                              </h3>
                            </div>

                            <Badge>
                              {
                                analysis
                                  .tool_plan
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

                        <div className="grid gap-3 sm:grid-cols-3">
                          <Link
                            href="/approvals"
                            className="flex items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-900 p-4 text-sm transition hover:bg-slate-800"
                          >
                            <ShieldCheck className="h-4 w-4 text-amber-400" />

                            Approval Queue
                          </Link>

                          <Link
                            href="/runs"
                            className="flex items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-900 p-4 text-sm transition hover:bg-slate-800"
                          >
                            <Workflow className="h-4 w-4 text-cyan-400" />

                            Audit Trail
                          </Link>

                          <button
                            onClick={resetForm}
                            className="flex items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-900 p-4 text-sm transition hover:bg-slate-800"
                          >
                            <RotateCcw className="h-4 w-4 text-emerald-400" />

                            New Test
                          </button>
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