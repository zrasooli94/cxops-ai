"use client";

import {
  ArrowLeft,
  Bot,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  LoaderCircle,
  Plus,
  RotateCcw,
  Send,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Workflow,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import {
  type FormEvent,
  useState,
} from "react";

import AppSidebar from "@/components/app-sidebar";

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
  const styles: Record<
    BadgeVariant,
    string
  > = {
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

    default:
      return "default";
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

function FieldLabel({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <label className="text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
      {children}
    </label>
  );
}

function InfoValue({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
        {label}
      </p>

      <p className="mt-2 text-sm font-medium text-slate-800">
        {value}
      </p>
    </div>
  );
}

export default function NewTicketPage() {
  const [subject, setSubject] =
    useState("");

  const [
    description,
    setDescription,
  ] = useState("");

  const [email, setEmail] =
    useState("");

  const [
    priority,
    setPriority,
  ] = useState<Priority>("normal");

  const [loading, setLoading] =
    useState(false);

  const [mode, setMode] =
    useState<
      "create" | "analyze" | null
    >(null);

  const [error, setError] =
    useState("");

  const [
    createdTicket,
    setCreatedTicket,
  ] =
    useState<CreatedTicket | null>(
      null,
    );

  const [
    analysis,
    setAnalysis,
  ] =
    useState<AgentAnalysis | null>(
      null,
    );

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

  async function requestAnalysis(
    ticketId: number,
  ) {
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

    return body as AgentAnalysis;
  }

  async function analyzeExistingTicket() {
    if (!createdTicket) {
      return;
    }

    setLoading(true);
    setMode("analyze");
    setError("");
    setAnalysis(null);

    try {
      const result =
        await requestAnalysis(
          createdTicket.id,
        );

      setAnalysis(result);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : `Ticket #${createdTicket.id} exists, but AI analysis failed.`,
      );
    } finally {
      setLoading(false);
      setMode(null);
    }
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

      const result =
        await requestAnalysis(
          created.id,
        );

      setAnalysis(result);
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
    <div className="min-h-screen">
      <AppSidebar active="/tickets" />

      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                New Test Ticket
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Safe agent demonstration
              </p>
            </div>

            <div className="flex items-center gap-3">
              <div className="hidden items-center gap-2 rounded-full border border-slate-200 bg-white/80 px-4 py-2 text-[11px] text-slate-500 shadow-sm sm:flex">
                <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]" />
                Demo mode ready
              </div>

              <Link
                href="/tickets"
                className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2.5 text-xs font-medium text-slate-600 shadow-sm transition hover:border-violet-200 hover:text-violet-600"
              >
                <ArrowLeft className="h-4 w-4" />
                Tickets
              </Link>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Live Agent Demo
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Create a case.
              <span className="gradient-text">
                {" "}
                Test the agent.
              </span>
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Create a completely new
              support scenario and run it
              through the same LangGraph,
              RAG and risk-control pipeline
              used by CXOps AI.
            </p>
          </section>

          {error && (
            <div className="mb-6 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <XCircle className="mt-0.5 h-5 w-5 shrink-0" />

              <div>
                <p>{error}</p>

                {createdTicket && (
                  <p className="mt-1 text-xs text-red-500/80">
                    Ticket #
                    {createdTicket.id} exists
                    in CXOps.
                  </p>
                )}
              </div>
            </div>
          )}

          <div className="grid gap-6 xl:grid-cols-[420px_minmax(0,1fr)]">
            <section className="self-start xl:sticky xl:top-[96px]">
              <form
                onSubmit={handleSubmit}
                className="app-panel rounded-[22px] p-6"
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-blue-500 text-white shadow-[0_12px_28px_rgba(105,87,255,0.2)]">
                    <Plus className="h-5 w-5" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-950">
                      Customer Case
                    </h2>

                    <p className="text-xs text-slate-400">
                      Build a test scenario
                    </p>
                  </div>
                </div>

                <p className="mt-5 text-sm leading-6 text-slate-500">
                  Enter any customer-support
                  request you want to use for
                  the live AI demonstration.
                </p>

                <div className="mt-6">
                  <FieldLabel>
                    Subject *
                  </FieldLabel>

                  <input
                    value={subject}
                    onChange={(event) =>
                      setSubject(
                        event.target.value,
                      )
                    }
                    maxLength={255}
                    placeholder="Example: Withdrawal pending for three days"
                    className="mt-2 w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                  />

                  <div className="mt-1.5 text-right text-[10px] text-slate-400">
                    {subject.length}/255
                  </div>
                </div>

                <div className="mt-4">
                  <FieldLabel>
                    Customer message *
                  </FieldLabel>

                  <textarea
                    value={description}
                    onChange={(event) =>
                      setDescription(
                        event.target.value,
                      )
                    }
                    rows={8}
                    placeholder="Describe the customer's issue..."
                    className="mt-2 w-full resize-none rounded-2xl border border-slate-200 bg-[#fbfcff] p-4 text-sm leading-6 text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                  />
                </div>

                <div className="mt-4">
                  <FieldLabel>
                    Requester email
                  </FieldLabel>

                  <input
                    type="email"
                    value={email}
                    onChange={(event) =>
                      setEmail(
                        event.target.value,
                      )
                    }
                    placeholder="tester@example.com"
                    className="mt-2 w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                  />

                  <p className="mt-2 text-[11px] leading-5 text-slate-400">
                    Optional for local test
                    tickets.
                  </p>
                </div>

                <div className="mt-4">
                  <FieldLabel>
                    Priority
                  </FieldLabel>

                  <select
                    value={priority}
                    onChange={(event) =>
                      setPriority(
                        event.target
                          .value as Priority,
                      )
                    }
                    className="mt-2 w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-violet-300 focus:ring-4 focus:ring-violet-100/50"
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
                      void createTicket(
                        true,
                      )
                    }
                    disabled={
                      loading ||
                      !subject.trim() ||
                      !description.trim()
                    }
                    className="flex items-center justify-center gap-2.5 rounded-full bg-gradient-to-r from-[#765cff] to-[#508cff] px-5 py-3.5 text-sm font-medium text-white shadow-[0_10px_28px_rgba(105,87,255,0.2)] transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {loading &&
                    mode ===
                      "analyze" ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <Sparkles className="h-4 w-4" />
                    )}

                    {loading &&
                    mode ===
                      "analyze"
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
                    className="flex items-center justify-center gap-2 rounded-full border border-slate-200 bg-white px-5 py-3.5 text-sm font-medium text-slate-700 transition hover:-translate-y-0.5 hover:border-violet-200 hover:text-violet-600 disabled:opacity-50"
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

              <div className="mt-5 rounded-[20px] border border-amber-200 bg-amber-50/60 p-5">
                <div className="flex gap-3">
                  <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />

                  <div>
                    <p className="text-sm font-medium text-amber-800">
                      Safe test ticket
                    </p>

                    <p className="mt-1 text-xs leading-5 text-amber-700/80">
                      This record is stored in
                      the CXOps database without
                      a Zendesk external ID. It
                      is intended for testing the
                      AI decision pipeline, not
                      live Zendesk execution.
                    </p>
                  </div>
                </div>
              </div>
            </section>

            <section className="min-w-0">
              {!createdTicket &&
                !loading && (
                  <div className="app-panel relative flex min-h-[640px] overflow-hidden rounded-[22px]">
                    <div className="soft-grid absolute inset-0 opacity-20" />

                    <div className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-violet-300/10 blur-3xl" />

                    <div className="pointer-events-none absolute -bottom-32 -left-32 h-96 w-96 rounded-full bg-blue-300/10 blur-3xl" />

                    <div className="relative m-auto max-w-xl px-8 text-center">
                      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[22px] bg-gradient-to-br from-violet-500 to-blue-500 text-white shadow-[0_20px_55px_rgba(98,82,255,0.22)]">
                        <Bot className="h-7 w-7" />
                      </div>

                      <p className="mt-7 text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-500">
                        Live Tester Workspace
                      </p>

                      <h2 className="mt-4 text-3xl font-light tracking-[-0.05em] text-slate-950">
                        Test the whole agent
                        pipeline safely.
                      </h2>

                      <p className="mx-auto mt-4 max-w-lg text-sm leading-7 text-slate-500">
                        Create a custom issue
                        and send it through the
                        same knowledge gate,
                        RAG, decision and
                        authorization workflow
                        used elsewhere in CXOps.
                      </p>

                      <div className="mt-7 flex flex-wrap justify-center gap-2">
                        <Badge variant="violet">
                          PostgreSQL
                        </Badge>

                        <Badge variant="info">
                          LangGraph
                        </Badge>

                        <Badge>
                          RAG
                        </Badge>

                        <Badge variant="warning">
                          Risk Controls
                        </Badge>
                      </div>
                    </div>
                  </div>
                )}

              {loading &&
                !createdTicket && (
                  <div className="app-panel relative flex min-h-[640px] overflow-hidden rounded-[22px]">
                    <div className="absolute inset-0 bg-gradient-to-br from-violet-50/70 via-white to-blue-50/55" />
                    <div className="soft-grid absolute inset-0 opacity-25" />

                    <div className="relative m-auto text-center">
                      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[22px] bg-white shadow-[0_20px_55px_rgba(98,82,255,0.14)]">
                        <LoaderCircle className="h-8 w-8 animate-spin text-violet-500" />
                      </div>

                      <p className="mt-5 font-medium text-slate-800">
                        {mode === "analyze"
                          ? "Creating test ticket..."
                          : "Saving test ticket..."}
                      </p>

                      <p className="mt-2 text-sm text-slate-400">
                        CXOps PostgreSQL
                      </p>
                    </div>
                  </div>
                )}

              {createdTicket && (
                <div className="space-y-6">
                  <section className="app-panel overflow-hidden rounded-[22px]">
                    <div className="border-b border-slate-200/70 bg-gradient-to-r from-emerald-50/65 via-white to-blue-50/45 p-6 md:p-7">
                      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                        <div>
                          <div className="flex items-center gap-3">
                            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
                              <CheckCircle2 className="h-5 w-5" />
                            </div>

                            <div>
                              <h2 className="font-medium text-slate-950">
                                Ticket Created
                              </h2>

                              <p className="text-xs text-slate-400">
                                CXOps Ticket #
                                {
                                  createdTicket.id
                                }
                              </p>
                            </div>
                          </div>
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

                          <Badge variant="violet">
                            Local test
                          </Badge>
                        </div>
                      </div>
                    </div>

                    <div className="p-6 md:p-7">
                      <h3 className="text-xl font-medium tracking-[-0.03em] text-slate-950">
                        {
                          createdTicket.subject
                        }
                      </h3>

                      <div className="mt-4 rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5">
                        <p className="whitespace-pre-wrap text-sm leading-7 text-slate-600">
                          {
                            createdTicket.description
                          }
                        </p>
                      </div>

                      <div className="mt-4 grid gap-3 sm:grid-cols-3">
                        <InfoValue
                          label="Source"
                          value={
                            createdTicket.source
                          }
                        />

                        <InfoValue
                          label="Category"
                          value={
                            createdTicket.category ??
                            "Unclassified"
                          }
                        />

                        <InfoValue
                          label="Assigned team"
                          value={
                            createdTicket.assigned_team ??
                            "Unassigned"
                          }
                        />
                      </div>

                      <div className="mt-5 flex flex-wrap gap-3">
                        <Link
                          href="/tickets"
                          className="rounded-full border border-slate-200 bg-white px-4 py-2.5 text-xs font-medium text-slate-600 transition hover:border-violet-200 hover:text-violet-600"
                        >
                          Open Tickets
                        </Link>

                        <Link
                          href="/agent"
                          className="rounded-full border border-slate-200 bg-white px-4 py-2.5 text-xs font-medium text-slate-600 transition hover:border-violet-200 hover:text-violet-600"
                        >
                          AI Agent
                        </Link>

                        <button
                          type="button"
                          onClick={
                            resetForm
                          }
                          className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2.5 text-xs font-medium text-slate-600 transition hover:border-emerald-200 hover:text-emerald-600"
                        >
                          <RotateCcw className="h-4 w-4" />
                          Create Another
                        </button>
                      </div>
                    </div>
                  </section>

                  {loading &&
                    mode ===
                      "analyze" &&
                    !analysis && (
                      <section className="app-panel relative flex min-h-[280px] overflow-hidden rounded-[22px]">
                        <div className="absolute inset-0 bg-gradient-to-br from-violet-50/70 via-white to-blue-50/50" />

                        <div className="relative m-auto text-center">
                          <LoaderCircle className="mx-auto h-8 w-8 animate-spin text-violet-500" />

                          <p className="mt-4 font-medium text-slate-800">
                            CXOps Agent is
                            analyzing Ticket #
                            {
                              createdTicket.id
                            }
                          </p>

                          <p className="mt-2 text-sm text-slate-400">
                            Knowledge gate →
                            RAG → decision →
                            authorization
                          </p>
                        </div>
                      </section>
                    )}

                  {!analysis &&
                    !loading && (
                      <section className="rounded-[22px] border border-violet-200 bg-gradient-to-r from-violet-50/65 via-white to-blue-50/55 p-6">
                        <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-center">
                          <div>
                            <div className="flex items-center gap-3">
                              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white text-violet-500 shadow-sm">
                                <Bot className="h-5 w-5" />
                              </div>

                              <div>
                                <p className="font-medium text-slate-900">
                                  Ticket ready for AI
                                </p>

                                <p className="mt-1 text-sm text-slate-500">
                                  Analyze the existing
                                  test ticket without
                                  creating another one.
                                </p>
                              </div>
                            </div>
                          </div>

                          <button
                            type="button"
                            onClick={() =>
                              void analyzeExistingTicket()
                            }
                            className="flex items-center justify-center gap-2 rounded-full bg-[#111827] px-5 py-3 text-sm font-medium text-white transition hover:-translate-y-0.5 hover:bg-gradient-to-r hover:from-[#765cff] hover:to-[#508cff]"
                          >
                            <Send className="h-4 w-4" />
                            Analyze
                          </button>
                        </div>
                      </section>
                    )}

                  {analysis && (
                    <>
                      <section className="app-panel overflow-hidden rounded-[22px]">
                        <div className="border-b border-slate-200/70 bg-gradient-to-r from-violet-50/70 via-white to-blue-50/55 p-6 md:p-7">
                          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                            <div>
                              <div className="flex items-center gap-3">
                                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-blue-500 text-white">
                                  <Sparkles className="h-5 w-5" />
                                </div>

                                <div>
                                  <h2 className="font-medium text-slate-950">
                                    CXOps AI Decision
                                  </h2>

                                  <p className="mt-1 break-all font-mono text-[10px] text-slate-400">
                                    {
                                      analysis.run_id
                                    }
                                  </p>
                                </div>
                              </div>
                            </div>

                            <div className="flex flex-wrap gap-2">
                              <Badge
                                variant={actionVariant(
                                  analysis.decision
                                    .action,
                                )}
                              >
                                {formatText(
                                  analysis.decision
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
                                  No approval
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
                            <InfoValue
                              label="Recommended team"
                              value={
                                analysis.decision
                                  .recommended_team ??
                                "—"
                              }
                            />

                            <InfoValue
                              label="Recommended priority"
                              value={
                                analysis.decision
                                  .recommended_priority ??
                                "—"
                              }
                            />
                          </div>

                          {analysis.decision
                            .response_draft && (
                            <div className="mt-4 rounded-2xl border border-blue-100 bg-blue-50/45 p-5">
                              <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-blue-500">
                                Proposed response
                              </p>

                              <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-600">
                                {
                                  analysis.decision
                                    .response_draft
                                }
                              </p>
                            </div>
                          )}

                          {analysis.decision
                            .requires_human_approval && (
                            <div className="mt-4 flex gap-3 rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
                              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />

                              <div>
                                <p className="text-sm font-medium text-amber-800">
                                  Human-in-the-loop
                                  control activated
                                </p>

                                <p className="mt-1 text-xs leading-5 text-amber-700">
                                  Sensitive actions
                                  remain blocked until
                                  reviewed in the
                                  Approval Queue.
                                </p>
                              </div>
                            </div>
                          )}

                          {analysis.auto_queued && (
                            <div className="mt-4 flex gap-3 rounded-2xl border border-emerald-200 bg-emerald-50/70 p-4">
                              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />

                              <div>
                                <p className="text-sm font-medium text-emerald-800">
                                  Autonomous action
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

                          {!analysis.auto_queued &&
                            !analysis.decision
                              .requires_human_approval && (
                              <div className="mt-4 flex gap-3 rounded-2xl border border-slate-200 bg-slate-50/75 p-4">
                                <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />

                                <div>
                                  <p className="text-sm font-medium text-slate-700">
                                    No external execution
                                    queued
                                  </p>

                                  <p className="mt-1 text-xs leading-5 text-slate-500">
                                    Local test tickets do
                                    not have a Zendesk
                                    execution target.
                                  </p>
                                </div>
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
                              LangGraph Path
                            </h2>

                            <p className="text-xs text-slate-400">
                              Nodes traversed during
                              this analysis
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
                                Knowledge used during
                                reasoning
                              </p>
                            </div>
                          </div>

                          <Badge variant="info">
                            {
                              analysis.sources.length
                            }{" "}
                            sources
                          </Badge>
                        </div>

                        {analysis.sources.length ===
                        0 ? (
                          <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50/70 p-5 text-sm text-slate-500">
                            No knowledge retrieval
                            was required.
                          </div>
                        ) : (
                          <div
                            data-lenis-prevent
                            className="mt-6 max-h-[680px] space-y-4 overflow-y-auto overscroll-contain pr-1"
                          >
                            {analysis.sources.map(
                              (source) => (
                                <div
                                  key={`${source.source_id}-${source.chunk_id}`}
                                  className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5"
                                >
                                  <div className="flex flex-col justify-between gap-3 sm:flex-row">
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
                                Risk and authorization
                                plan
                              </p>
                            </div>
                          </div>

                          <Badge variant="violet">
                            {
                              analysis.tool_plan
                                .length
                            }{" "}
                            tools
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
                                  <p className="font-mono text-sm font-medium text-violet-600">
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
                                        Approval required
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

                      <div className="grid gap-3 sm:grid-cols-3">
                        <Link
                          href="/approvals"
                          className="app-panel group flex items-center justify-between rounded-[18px] p-5 transition hover:-translate-y-0.5 hover:border-amber-200"
                        >
                          <span className="text-sm font-medium text-slate-800">
                            Approval Queue
                          </span>

                          <ChevronRight className="h-4 w-4 text-slate-300 group-hover:text-amber-500" />
                        </Link>

                        <Link
                          href="/runs"
                          className="app-panel group flex items-center justify-between rounded-[18px] p-5 transition hover:-translate-y-0.5 hover:border-violet-200"
                        >
                          <span className="text-sm font-medium text-slate-800">
                            Audit Trail
                          </span>

                          <ChevronRight className="h-4 w-4 text-slate-300 group-hover:text-violet-500" />
                        </Link>

                        <button
                          type="button"
                          onClick={
                            resetForm
                          }
                          className="app-panel group flex items-center justify-between rounded-[18px] p-5 text-left transition hover:-translate-y-0.5 hover:border-emerald-200"
                        >
                          <span className="text-sm font-medium text-slate-800">
                            New Test
                          </span>

                          <RotateCcw className="h-4 w-4 text-slate-300 group-hover:text-emerald-500" />
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </section>
          </div>

          <section className="mt-7 rounded-[22px] border border-violet-100 bg-gradient-to-r from-violet-50/60 via-white to-blue-50/55 p-6">
            <div className="flex flex-col justify-between gap-5 md:flex-row md:items-center">
              <div>
                <p className="text-sm font-medium text-slate-900">
                  Safe portfolio demonstration
                </p>

                <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
                  Test tickets exercise the
                  production decision pipeline
                  while remaining separate from
                  real Zendesk execution targets.
                </p>
              </div>

              <div className="flex items-center gap-2 text-xs text-emerald-600">
                <CircleDot className="h-3.5 w-3.5" />
                Safety boundary active
              </div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
