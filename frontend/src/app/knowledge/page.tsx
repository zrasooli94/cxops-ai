"use client";

import {
  Bot,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Database,
  FileText,
  Layers3,
  LoaderCircle,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Upload,
  XCircle,
} from "lucide-react";
import {
  type FormEvent,
  useState,
} from "react";

import AppSidebar from "@/components/app-sidebar";

type RAGSource = {
  source_id: string;
  chunk_id: number;
  document_id: number;
  title: string;
  content: string;
  similarity: number;
};

type RAGAnswer = {
  request_id: string;
  answer: string;
  grounded: boolean;
  sources: RAGSource[];
  retrieval_count: number;
  best_similarity: number | null;
};

type SearchResult = {
  chunk_id: number;
  document_id: number;
  content: string;
  distance: number;
  similarity: number;
  metadata: Record<string, unknown>;
};

type IngestionResult = {
  document_id: number;
  title: string;
  chunks_created: number;
  duplicate: boolean;
};

type UploadResult = {
  document_id: number;
  filename: string;
  title: string;
  chunks_created: number;
  duplicate: boolean;
};

type Tab =
  | "rag"
  | "search"
  | "ingest";

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

function metadataTitle(
  metadata: Record<string, unknown>,
) {
  const value = metadata.title;

  if (typeof value === "string") {
    return value;
  }

  return null;
}

function TechnologyCard({
  title,
  value,
  description,
  icon: Icon,
  tone,
}: {
  title: string;
  value: string;
  description: string;
  icon: typeof Database;
  tone: "violet" | "blue" | "emerald";
}) {
  const styles = {
    violet:
      "bg-violet-50 text-violet-500",
    blue: "bg-blue-50 text-blue-500",
    emerald:
      "bg-emerald-50 text-emerald-500",
  }[tone];

  return (
    <div className="app-panel rounded-[20px] p-6">
      <div
        className={`flex h-10 w-10 items-center justify-center rounded-xl ${styles}`}
      >
        <Icon className="h-5 w-5" />
      </div>

      <p className="mt-5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
        {title}
      </p>

      <p className="mt-2 font-medium tracking-[-0.025em] text-slate-900">
        {value}
      </p>

      <p className="mt-2 text-xs leading-5 text-slate-500">
        {description}
      </p>
    </div>
  );
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

export default function KnowledgePage() {
  const [tab, setTab] =
    useState<Tab>("rag");

  const [question, setQuestion] =
    useState(
      "How long does a normal withdrawal take?",
    );

  const [topK, setTopK] =
    useState(5);

  const [answer, setAnswer] =
    useState<RAGAnswer | null>(null);

  const [
    answerLoading,
    setAnswerLoading,
  ] = useState(false);

  const [
    answerError,
    setAnswerError,
  ] = useState("");

  const [
    searchQuery,
    setSearchQuery,
  ] = useState(
    "withdrawal processing time",
  );

  const [
    searchLimit,
    setSearchLimit,
  ] = useState(5);

  const [
    searchResults,
    setSearchResults,
  ] = useState<SearchResult[]>([]);

  const [
    searchLoading,
    setSearchLoading,
  ] = useState(false);

  const [
    searchError,
    setSearchError,
  ] = useState("");

  const [
    documentTitle,
    setDocumentTitle,
  ] = useState("");

  const [
    documentContent,
    setDocumentContent,
  ] = useState("");

  const [
    documentSource,
    setDocumentSource,
  ] = useState("manual");

  const [
    ingestLoading,
    setIngestLoading,
  ] = useState(false);

  const [
    ingestResult,
    setIngestResult,
  ] = useState<IngestionResult | null>(
    null,
  );

  const [
    ingestError,
    setIngestError,
  ] = useState("");

  const [
    uploadFile,
    setUploadFile,
  ] = useState<File | null>(null);

  const [
    uploadTitle,
    setUploadTitle,
  ] = useState("");

  const [
    uploadLoading,
    setUploadLoading,
  ] = useState(false);

  const [
    uploadResult,
    setUploadResult,
  ] = useState<UploadResult | null>(
    null,
  );

  const [
    uploadError,
    setUploadError,
  ] = useState("");

  async function askKnowledge(
    event?: FormEvent,
  ) {
    event?.preventDefault();

    if (question.trim().length < 2) {
      return;
    }

    setAnswerLoading(true);
    setAnswerError("");
    setAnswer(null);

    try {
      const response = await fetch(
        "/api/backend/knowledge/answer",
        {
          method: "POST",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify({
            question: question.trim(),
            top_k: topK,
          }),
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        throw new Error(
          body?.detail ??
            `RAG API returned ${response.status}`,
        );
      }

      setAnswer(body as RAGAnswer);
    } catch (err) {
      setAnswerError(
        err instanceof Error
          ? err.message
          : "RAG request failed.",
      );
    } finally {
      setAnswerLoading(false);
    }
  }

  async function runSearch(
    event?: FormEvent,
  ) {
    event?.preventDefault();

    if (!searchQuery.trim()) {
      return;
    }

    setSearchLoading(true);
    setSearchError("");
    setSearchResults([]);

    try {
      const response = await fetch(
        "/api/backend/knowledge/search",
        {
          method: "POST",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify({
            query: searchQuery.trim(),
            limit: searchLimit,
          }),
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        throw new Error(
          body?.detail ??
            `Search API returned ${response.status}`,
        );
      }

      setSearchResults(
        body as SearchResult[],
      );
    } catch (err) {
      setSearchError(
        err instanceof Error
          ? err.message
          : "Knowledge search failed.",
      );
    } finally {
      setSearchLoading(false);
    }
  }

  async function ingestDocument(
    event: FormEvent,
  ) {
    event.preventDefault();

    if (
      !documentTitle.trim() ||
      !documentContent.trim()
    ) {
      return;
    }

    setIngestLoading(true);
    setIngestError("");
    setIngestResult(null);

    try {
      const response = await fetch(
        "/api/backend/knowledge/documents",
        {
          method: "POST",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify({
            title:
              documentTitle.trim(),
            content:
              documentContent.trim(),
            source:
              documentSource.trim() ||
              "manual",
            source_uri: null,
            metadata: {
              ingested_from:
                "cxops-control-center",
            },
          }),
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        throw new Error(
          body?.detail ??
            `Ingestion API returned ${response.status}`,
        );
      }

      setIngestResult(
        body as IngestionResult,
      );
    } catch (err) {
      setIngestError(
        err instanceof Error
          ? err.message
          : "Document ingestion failed.",
      );
    } finally {
      setIngestLoading(false);
    }
  }

  async function uploadDocument(
    event: FormEvent,
  ) {
    event.preventDefault();

    if (!uploadFile) {
      return;
    }

    setUploadLoading(true);
    setUploadError("");
    setUploadResult(null);

    try {
      const formData =
        new FormData();

      formData.append(
        "file",
        uploadFile,
      );

      if (uploadTitle.trim()) {
        formData.append(
          "title",
          uploadTitle.trim(),
        );
      }

      formData.append(
        "source",
        "control-center-upload",
      );

      const response = await fetch(
        "/api/backend/knowledge/documents/upload",
        {
          method: "POST",
          body: formData,
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        throw new Error(
          body?.detail ??
            `Upload API returned ${response.status}`,
        );
      }

      setUploadResult(
        body as UploadResult,
      );
    } catch (err) {
      setUploadError(
        err instanceof Error
          ? err.message
          : "File upload failed.",
      );
    } finally {
      setUploadLoading(false);
    }
  }

  const examples = [
    "How long does a withdrawal normally take?",
    "What happens if a deposit is missing?",
    "What are the identity verification requirements?",
    "What is the company vacation policy?",
  ];

  return (
    <div className="min-h-screen">
      <AppSidebar active="/knowledge" />

      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Knowledge
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Retrieval-Augmented Generation
              </p>
            </div>

            <div className="hidden items-center gap-2 rounded-full border border-slate-200 bg-white/80 px-4 py-2 text-[11px] text-slate-500 shadow-sm sm:flex">
              <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]" />
              Vector search active
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Retrieval-Augmented Generation
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Knowledge / RAG
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Query the live vector knowledge
              base, inspect the exact evidence
              used by generation, and add new
              policy documents to the retrieval
              pipeline.
            </p>
          </section>

          <section className="mb-7 grid gap-4 md:grid-cols-3">
            <TechnologyCard
              title="Vector store"
              value="PostgreSQL + pgvector"
              description="Persistent semantic vectors stored alongside operational data."
              icon={Database}
              tone="violet"
            />

            <TechnologyCard
              title="Retrieval"
              value="Semantic similarity"
              description="Relevant policy chunks are ranked by vector similarity."
              icon={BrainCircuit}
              tone="blue"
            />

            <TechnologyCard
              title="Generation"
              value="Grounded + cited"
              description="Answers expose supporting evidence rather than hiding retrieval."
              icon={ShieldCheck}
              tone="emerald"
            />
          </section>

          <div className="app-panel mb-7 inline-flex flex-wrap gap-1 rounded-[18px] p-1.5">
            {[
              {
                id: "rag" as Tab,
                label: "RAG Playground",
                icon: Sparkles,
              },
              {
                id: "search" as Tab,
                label: "Semantic Search",
                icon: Search,
              },
              {
                id: "ingest" as Tab,
                label: "Ingest Knowledge",
                icon: Upload,
              },
            ].map((item) => {
              const Icon = item.icon;
              const active =
                tab === item.id;

              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() =>
                    setTab(item.id)
                  }
                  className={`inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs font-medium transition ${
                    active
                      ? "bg-[#111827] text-white shadow-[0_8px_22px_rgba(17,24,39,0.12)]"
                      : "text-slate-500 hover:bg-slate-50 hover:text-slate-900"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  {item.label}
                </button>
              );
            })}
          </div>

          {tab === "rag" && (
            <div className="grid gap-6 xl:grid-cols-[390px_minmax(0,1fr)]">
              <section className="app-panel self-start rounded-[22px] p-6 xl:sticky xl:top-[96px]">
                <div className="flex items-center gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-blue-500 text-white shadow-[0_12px_28px_rgba(105,87,255,0.2)]">
                    <Sparkles className="h-5 w-5" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-950">
                      Ask Knowledge Base
                    </h2>

                    <p className="text-xs text-slate-400">
                      Grounded policy answer
                    </p>
                  </div>
                </div>

                <p className="mt-5 text-sm leading-6 text-slate-500">
                  Ask a customer-support policy
                  question. CXOps retrieves
                  relevant chunks before the
                  answer is generated.
                </p>

                <form
                  onSubmit={askKnowledge}
                  className="mt-6"
                >
                  <FieldLabel>
                    Question
                  </FieldLabel>

                  <textarea
                    value={question}
                    onChange={(event) =>
                      setQuestion(
                        event.target.value,
                      )
                    }
                    rows={7}
                    maxLength={2000}
                    placeholder="Ask a policy question..."
                    className="mt-2 w-full resize-none rounded-2xl border border-slate-200 bg-[#fbfcff] p-4 text-sm leading-6 text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                  />

                  <div className="mt-4">
                    <FieldLabel>
                      Maximum retrieval candidates
                    </FieldLabel>

                    <select
                      value={topK}
                      onChange={(event) =>
                        setTopK(
                          Number(
                            event.target.value,
                          ),
                        )
                      }
                      className="mt-2 w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-violet-300 focus:ring-4 focus:ring-violet-100/50"
                    >
                      {[
                        1,
                        2,
                        3,
                        4,
                        5,
                        6,
                        8,
                        10,
                      ].map((value) => (
                        <option
                          key={value}
                          value={value}
                        >
                          Top {value}
                        </option>
                      ))}
                    </select>
                  </div>

                  <button
                    type="submit"
                    disabled={
                      answerLoading ||
                      question.trim().length <
                        2
                    }
                    className="mt-5 flex w-full items-center justify-center gap-2.5 rounded-full bg-[#111827] px-5 py-3.5 text-sm font-medium text-white shadow-[0_12px_30px_rgba(17,24,39,0.15)] transition hover:-translate-y-0.5 hover:bg-gradient-to-r hover:from-[#765cff] hover:to-[#508cff] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {answerLoading ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <Send className="h-4 w-4" />
                    )}

                    {answerLoading
                      ? "Retrieving & generating..."
                      : "Ask CXOps RAG"}
                  </button>
                </form>

                <div className="mt-6 border-t border-slate-200/70 pt-5">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                    Example questions
                  </p>

                  <div className="mt-3 space-y-2">
                    {examples.map(
                      (example) => (
                        <button
                          key={example}
                          type="button"
                          onClick={() =>
                            setQuestion(
                              example,
                            )
                          }
                          className="group flex w-full items-center justify-between gap-3 rounded-xl border border-slate-200/80 bg-white/70 px-3.5 py-3 text-left text-xs leading-5 text-slate-500 transition hover:border-violet-200 hover:bg-violet-50/40 hover:text-slate-800"
                        >
                          <span>
                            {example}
                          </span>

                          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-violet-400" />
                        </button>
                      ),
                    )}
                  </div>
                </div>
              </section>

              <section className="min-w-0">
                {answerError && (
                  <div className="flex items-start gap-3 rounded-[20px] border border-red-200 bg-red-50/80 p-5 text-sm text-red-700">
                    <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
                    {answerError}
                  </div>
                )}

                {answerLoading && (
                  <div className="app-panel relative flex min-h-[520px] overflow-hidden rounded-[22px]">
                    <div className="absolute inset-0 bg-gradient-to-br from-violet-50/80 via-white to-blue-50/60" />
                    <div className="soft-grid absolute inset-0 opacity-25" />

                    <div className="relative m-auto px-8 text-center">
                      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[22px] bg-white shadow-[0_20px_55px_rgba(98,82,255,0.14)]">
                        <LoaderCircle className="h-8 w-8 animate-spin text-violet-500" />
                      </div>

                      <h3 className="mt-6 text-lg font-medium text-slate-950">
                        Running RAG pipeline
                      </h3>

                      <p className="mt-2 text-sm text-slate-500">
                        Embedding → pgvector
                        retrieval → grounding →
                        generation
                      </p>
                    </div>
                  </div>
                )}

                {!answer &&
                  !answerLoading &&
                  !answerError && (
                    <div className="app-panel relative flex min-h-[520px] overflow-hidden rounded-[22px]">
                      <div className="soft-grid absolute inset-0 opacity-20" />

                      <div className="relative m-auto max-w-lg px-8 text-center">
                        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[22px] bg-gradient-to-br from-violet-50 to-blue-50 text-violet-500">
                          <BrainCircuit className="h-7 w-7" />
                        </div>

                        <p className="mt-6 text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-500">
                          RAG Playground
                        </p>

                        <h2 className="mt-4 text-3xl font-light tracking-[-0.045em] text-slate-950">
                          Inspect what the AI
                          actually knows.
                        </h2>

                        <p className="mt-4 text-sm leading-7 text-slate-500">
                          Ask a question to view
                          both the generated answer
                          and the exact knowledge
                          chunks used as evidence.
                        </p>
                      </div>
                    </div>
                  )}

                {answer &&
                  !answerLoading && (
                    <div className="space-y-6">
                      <section className="app-panel overflow-hidden rounded-[22px]">
                        <div className="border-b border-slate-200/70 bg-gradient-to-r from-violet-50/70 via-white to-blue-50/55 p-6 md:p-7">
                          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                            <div className="flex items-center gap-3">
                              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-blue-500 text-white">
                                <Bot className="h-5 w-5" />
                              </div>

                              <div>
                                <h2 className="font-medium text-slate-950">
                                  Grounded Answer
                                </h2>

                                <p className="text-xs text-slate-400">
                                  Generated from retrieved evidence
                                </p>
                              </div>
                            </div>

                            {answer.grounded ? (
                              <Badge variant="success">
                                Grounded
                              </Badge>
                            ) : (
                              <Badge variant="warning">
                                Insufficient grounding
                              </Badge>
                            )}
                          </div>
                        </div>

                        <div className="p-6 md:p-7">
                          <p className="whitespace-pre-wrap text-sm leading-8 text-slate-600">
                            {answer.answer}
                          </p>

                          <div className="mt-7 grid gap-3 sm:grid-cols-3">
                            <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
                              <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">
                                Retrieved sources
                              </p>

                              <p className="editorial-number mt-2 text-2xl font-medium text-slate-950">
                                {
                                  answer.retrieval_count
                                }
                              </p>
                            </div>

                            <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
                              <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">
                                Best similarity
                              </p>

                              <p className="editorial-number mt-2 text-2xl font-medium text-slate-950">
                                {answer.best_similarity !==
                                null
                                  ? `${(
                                      answer.best_similarity *
                                      100
                                    ).toFixed(
                                      1,
                                    )}%`
                                  : "—"}
                              </p>
                            </div>

                            <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
                              <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">
                                Grounding
                              </p>

                              <p
                                className={`mt-2 text-xl font-medium ${
                                  answer.grounded
                                    ? "text-emerald-600"
                                    : "text-amber-600"
                                }`}
                              >
                                {answer.grounded
                                  ? "Verified"
                                  : "Guarded"}
                              </p>
                            </div>
                          </div>

                          <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-3">
                            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">
                              Request ID
                            </p>

                            <p className="mt-1 break-all font-mono text-xs text-slate-500">
                              {answer.request_id}
                            </p>
                          </div>
                        </div>
                      </section>

                      <section className="app-panel rounded-[22px] p-6 md:p-7">
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex items-center gap-3">
                            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                              <Database className="h-5 w-5" />
                            </div>

                            <div>
                              <h2 className="font-medium text-slate-900">
                                Retrieved Evidence
                              </h2>

                              <p className="text-xs text-slate-400">
                                Knowledge supplied
                                to generation
                              </p>
                            </div>
                          </div>

                          <Badge variant="info">
                            {
                              answer.sources.length
                            }{" "}
                            source
                            {answer.sources
                              .length === 1
                              ? ""
                              : "s"}
                          </Badge>
                        </div>

                        {answer.sources.length ===
                        0 ? (
                          <div className="mt-6 rounded-2xl border border-amber-200 bg-amber-50/65 p-5">
                            <p className="font-medium text-amber-800">
                              No relevant policy
                              evidence found
                            </p>

                            <p className="mt-2 text-sm leading-6 text-amber-700">
                              CXOps should refuse to
                              invent company policy
                              when the knowledge base
                              cannot support the
                              answer.
                            </p>
                          </div>
                        ) : (
                          <div
                            data-lenis-prevent
                            className="mt-6 max-h-[760px] space-y-4 overflow-y-auto overscroll-contain pr-1"
                          >
                            {answer.sources.map(
                              (source) => (
                                <div
                                  key={`${source.source_id}-${source.chunk_id}`}
                                  className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5"
                                >
                                  <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                                    <div>
                                      <div className="flex flex-wrap items-center gap-2">
                                        <Badge variant="info">
                                          {
                                            source.source_id
                                          }
                                        </Badge>

                                        <p className="font-medium text-slate-800">
                                          {
                                            source.title
                                          }
                                        </p>
                                      </div>

                                      <p className="mt-2 text-xs text-slate-400">
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
                                      % similarity
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
                    </div>
                  )}
              </section>
            </div>
          )}

          {tab === "search" && (
            <div className="grid gap-6 xl:grid-cols-[390px_minmax(0,1fr)]">
              <section className="app-panel self-start rounded-[22px] p-6 xl:sticky xl:top-[96px]">
                <div className="flex items-center gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-blue-50 text-blue-500">
                    <Search className="h-5 w-5" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-950">
                      Vector Search
                    </h2>

                    <p className="text-xs text-slate-400">
                      Raw semantic retrieval
                    </p>
                  </div>
                </div>

                <p className="mt-5 text-sm leading-6 text-slate-500">
                  Inspect semantic retrieval
                  independently from the language
                  model generation layer.
                </p>

                <form
                  onSubmit={runSearch}
                  className="mt-6"
                >
                  <FieldLabel>
                    Search query
                  </FieldLabel>

                  <textarea
                    value={searchQuery}
                    onChange={(event) =>
                      setSearchQuery(
                        event.target.value,
                      )
                    }
                    rows={5}
                    className="mt-2 w-full resize-none rounded-2xl border border-slate-200 bg-[#fbfcff] p-4 text-sm text-slate-700 outline-none transition focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                  />

                  <div className="mt-4">
                    <FieldLabel>
                      Result limit
                    </FieldLabel>

                    <select
                      value={searchLimit}
                      onChange={(event) =>
                        setSearchLimit(
                          Number(
                            event.target.value,
                          ),
                        )
                      }
                      className="mt-2 w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-700 outline-none focus:border-violet-300 focus:ring-4 focus:ring-violet-100/50"
                    >
                      {[
                        1,
                        3,
                        5,
                        10,
                        20,
                      ].map((value) => (
                        <option
                          key={value}
                          value={value}
                        >
                          {value} results
                        </option>
                      ))}
                    </select>
                  </div>

                  <button
                    type="submit"
                    disabled={searchLoading}
                    className="mt-5 flex w-full items-center justify-center gap-2 rounded-full bg-[#111827] px-5 py-3.5 text-sm font-medium text-white transition hover:-translate-y-0.5 hover:bg-gradient-to-r hover:from-[#765cff] hover:to-[#508cff] disabled:opacity-50"
                  >
                    {searchLoading ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <Search className="h-4 w-4" />
                    )}

                    Search pgvector
                  </button>
                </form>
              </section>

              <section>
                {searchError && (
                  <div className="mb-5 flex gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
                    <XCircle className="h-5 w-5 shrink-0" />
                    {searchError}
                  </div>
                )}

                <div className="app-panel rounded-[22px] p-6 md:p-7">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                          <Layers3 className="h-5 w-5" />
                        </div>

                        <div>
                          <h2 className="font-medium text-slate-900">
                            Semantic Results
                          </h2>

                          <p className="text-xs text-slate-400">
                            Ranked directly by
                            vector similarity
                          </p>
                        </div>
                      </div>
                    </div>

                    <Badge variant="violet">
                      {searchResults.length}{" "}
                      result
                      {searchResults.length ===
                      1
                        ? ""
                        : "s"}
                    </Badge>
                  </div>

                  {searchLoading ? (
                    <div className="flex h-80 items-center justify-center">
                      <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
                    </div>
                  ) : searchResults.length ===
                    0 ? (
                    <div className="mt-6 flex min-h-[320px] items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50/50">
                      <div className="text-center">
                        <Search className="mx-auto h-8 w-8 text-slate-300" />

                        <p className="mt-3 text-sm text-slate-400">
                          Run a semantic search to
                          inspect pgvector results.
                        </p>
                      </div>
                    </div>
                  ) : (
                    <div
                      data-lenis-prevent
                      className="mt-6 max-h-[760px] space-y-4 overflow-y-auto overscroll-contain pr-1"
                    >
                      {searchResults.map(
                        (
                          result,
                          index,
                        ) => {
                          const title =
                            metadataTitle(
                              result.metadata,
                            );

                          return (
                            <div
                              key={
                                result.chunk_id
                              }
                              className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5"
                            >
                              <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                                <div>
                                  <p className="font-medium text-slate-800">
                                    #
                                    {index +
                                      1}{" "}
                                    {title
                                      ? `— ${title}`
                                      : ""}
                                  </p>

                                  <p className="mt-1 text-xs text-slate-400">
                                    Document{" "}
                                    {
                                      result.document_id
                                    }{" "}
                                    · Chunk{" "}
                                    {
                                      result.chunk_id
                                    }
                                  </p>
                                </div>

                                <div className="flex flex-wrap gap-2">
                                  <Badge variant="success">
                                    {(
                                      result.similarity *
                                      100
                                    ).toFixed(
                                      1,
                                    )}
                                    %
                                  </Badge>

                                  <Badge>
                                    distance{" "}
                                    {result.distance.toFixed(
                                      3,
                                    )}
                                  </Badge>
                                </div>
                              </div>

                              <p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-slate-500">
                                {
                                  result.content
                                }
                              </p>
                            </div>
                          );
                        },
                      )}
                    </div>
                  )}
                </div>
              </section>
            </div>
          )}

          {tab === "ingest" && (
            <div className="grid gap-6 xl:grid-cols-2">
              <section className="app-panel rounded-[22px] p-6 md:p-7">
                <div className="flex items-center gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-50 text-violet-500">
                    <FileText className="h-5 w-5" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-950">
                      Manual Knowledge Ingestion
                    </h2>

                    <p className="text-xs text-slate-400">
                      Create a knowledge document
                    </p>
                  </div>
                </div>

                <p className="mt-5 text-sm leading-6 text-slate-500">
                  Add a policy or operating
                  procedure directly to the live
                  RAG knowledge base.
                </p>

                <form
                  onSubmit={ingestDocument}
                  className="mt-6 space-y-4"
                >
                  <div>
                    <FieldLabel>
                      Document title
                    </FieldLabel>

                    <input
                      value={documentTitle}
                      onChange={(event) =>
                        setDocumentTitle(
                          event.target.value,
                        )
                      }
                      placeholder="Example: VIP Support Policy"
                      className="mt-2 w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                    />
                  </div>

                  <div>
                    <FieldLabel>
                      Source
                    </FieldLabel>

                    <input
                      value={documentSource}
                      onChange={(event) =>
                        setDocumentSource(
                          event.target.value,
                        )
                      }
                      className="mt-2 w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                    />
                  </div>

                  <div>
                    <FieldLabel>
                      Content
                    </FieldLabel>

                    <textarea
                      value={documentContent}
                      onChange={(event) =>
                        setDocumentContent(
                          event.target.value,
                        )
                      }
                      rows={12}
                      placeholder="Paste policy content..."
                      className="mt-2 w-full resize-none rounded-2xl border border-slate-200 bg-[#fbfcff] p-4 text-sm leading-6 text-slate-700 outline-none transition focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={ingestLoading}
                    className="flex w-full items-center justify-center gap-2 rounded-full bg-gradient-to-r from-[#765cff] to-[#508cff] px-5 py-3.5 text-sm font-medium text-white shadow-[0_10px_28px_rgba(105,87,255,0.2)] transition hover:-translate-y-0.5 disabled:opacity-50"
                  >
                    {ingestLoading ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <Database className="h-4 w-4" />
                    )}

                    Ingest & Embed
                  </button>
                </form>

                {ingestError && (
                  <div className="mt-5 flex gap-3 rounded-2xl border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
                    <XCircle className="h-5 w-5 shrink-0" />
                    {ingestError}
                  </div>
                )}

                {ingestResult && (
                  <div className="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50/65 p-5">
                    <div className="flex items-center gap-2">
                      <CheckCircle2 className="h-5 w-5 text-emerald-500" />

                      <p className="font-medium text-emerald-800">
                        {ingestResult.duplicate
                          ? "Document already exists"
                          : "Knowledge ingested"}
                      </p>
                    </div>

                    <div className="mt-4 space-y-2 text-sm text-emerald-700">
                      <p>
                        Document ID:{" "}
                        {
                          ingestResult.document_id
                        }
                      </p>

                      <p>
                        Title:{" "}
                        {
                          ingestResult.title
                        }
                      </p>

                      <p>
                        Chunks created:{" "}
                        {
                          ingestResult.chunks_created
                        }
                      </p>
                    </div>
                  </div>
                )}
              </section>

              <section className="app-panel rounded-[22px] p-6 md:p-7">
                <div className="flex items-center gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-blue-50 text-blue-500">
                    <Upload className="h-5 w-5" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-950">
                      Upload Knowledge Document
                    </h2>

                    <p className="text-xs text-slate-400">
                      PDF · TXT · Markdown
                    </p>
                  </div>
                </div>

                <p className="mt-5 text-sm leading-6 text-slate-500">
                  Upload a document. CXOps
                  extracts the text, chunks it,
                  creates embeddings and stores
                  the vectors for retrieval.
                </p>

                <form
                  onSubmit={uploadDocument}
                  className="mt-6"
                >
                  <label className="group block cursor-pointer rounded-[22px] border border-dashed border-violet-200 bg-gradient-to-br from-violet-50/55 via-white to-blue-50/45 p-10 text-center transition hover:border-violet-300">
                    <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-white text-violet-500 shadow-[0_12px_30px_rgba(96,82,255,0.1)] transition group-hover:-translate-y-0.5">
                      <Upload className="h-6 w-6" />
                    </div>

                    <p className="mt-4 text-sm font-medium text-slate-800">
                      {uploadFile
                        ? uploadFile.name
                        : "Choose a knowledge document"}
                    </p>

                    <p className="mt-2 text-xs text-slate-400">
                      PDF · TXT · Markdown
                    </p>

                    <input
                      type="file"
                      accept=".pdf,.txt,.md"
                      onChange={(event) =>
                        setUploadFile(
                          event.target
                            .files?.[0] ??
                            null,
                        )
                      }
                      className="hidden"
                    />
                  </label>

                  <div className="mt-5">
                    <FieldLabel>
                      Optional title
                    </FieldLabel>

                    <input
                      value={uploadTitle}
                      onChange={(event) =>
                        setUploadTitle(
                          event.target.value,
                        )
                      }
                      placeholder="Defaults to filename"
                      className="mt-2 w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={
                      uploadLoading ||
                      !uploadFile
                    }
                    className="mt-5 flex w-full items-center justify-center gap-2 rounded-full bg-[#111827] px-5 py-3.5 text-sm font-medium text-white transition hover:-translate-y-0.5 hover:bg-gradient-to-r hover:from-[#765cff] hover:to-[#508cff] disabled:opacity-50"
                  >
                    {uploadLoading ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <Upload className="h-4 w-4" />
                    )}

                    Upload & Embed
                  </button>
                </form>

                {uploadError && (
                  <div className="mt-5 flex gap-3 rounded-2xl border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
                    <XCircle className="h-5 w-5 shrink-0" />
                    {uploadError}
                  </div>
                )}

                {uploadResult && (
                  <div className="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50/65 p-5">
                    <div className="flex items-center gap-2">
                      <CheckCircle2 className="h-5 w-5 text-emerald-500" />

                      <p className="font-medium text-emerald-800">
                        {uploadResult.duplicate
                          ? "Document already exists"
                          : "Document embedded successfully"}
                      </p>
                    </div>

                    <div className="mt-4 space-y-2 text-sm text-emerald-700">
                      <p>
                        File:{" "}
                        {
                          uploadResult.filename
                        }
                      </p>

                      <p>
                        Document ID:{" "}
                        {
                          uploadResult.document_id
                        }
                      </p>

                      <p>
                        Chunks:{" "}
                        {
                          uploadResult.chunks_created
                        }
                      </p>
                    </div>
                  </div>
                )}

                <div className="mt-6 rounded-2xl border border-violet-100 bg-gradient-to-r from-violet-50/55 to-blue-50/45 p-5">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium text-slate-800">
                      Ingestion pipeline
                    </p>

                    <div className="flex items-center gap-2 text-[10px] text-emerald-600">
                      <CircleDot className="h-3 w-3" />
                      Active
                    </div>
                  </div>

                  <div className="mt-4 flex flex-wrap items-center gap-2">
                    {[
                      "Parse",
                      "Chunk",
                      "Embed",
                      "pgvector",
                    ].map(
                      (
                        item,
                        index,
                        items,
                      ) => (
                        <div
                          key={item}
                          className="flex items-center gap-2"
                        >
                          <span className="rounded-xl border border-white bg-white/80 px-3 py-2 text-xs font-medium text-slate-600 shadow-sm">
                            {item}
                          </span>

                          {index <
                            items.length -
                              1 && (
                            <ChevronRight className="h-4 w-4 text-violet-300" />
                          )}
                        </div>
                      ),
                    )}
                  </div>
                </div>
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
