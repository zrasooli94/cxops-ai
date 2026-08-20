"use client";

import {
  Activity,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Database,
  FileText,
  Gauge,
  LoaderCircle,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Ticket,
  Upload,
  Workflow,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { FormEvent, useState } from "react";

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

function metadataTitle(
  metadata: Record<string, unknown>,
) {
  const value = metadata.title;

  if (typeof value === "string") {
    return value;
  }

  return null;
}

export default function KnowledgePage() {
  const [tab, setTab] =
    useState<Tab>("rag");

  /*
   * RAG Playground
   */
  const [question, setQuestion] =
    useState(
      "How long does a normal withdrawal take?",
    );

  const [topK, setTopK] =
    useState(5);

  const [answer, setAnswer] =
    useState<RAGAnswer | null>(null);

  const [answerLoading, setAnswerLoading] =
    useState(false);

  const [answerError, setAnswerError] =
    useState("");

  /*
   * Semantic Search
   */
  const [searchQuery, setSearchQuery] =
    useState(
      "withdrawal processing time",
    );

  const [searchLimit, setSearchLimit] =
    useState(5);

  const [searchResults, setSearchResults] =
    useState<SearchResult[]>([]);

  const [searchLoading, setSearchLoading] =
    useState(false);

  const [searchError, setSearchError] =
    useState("");

  /*
   * Manual ingestion
   */
  const [documentTitle, setDocumentTitle] =
    useState("");

  const [
    documentContent,
    setDocumentContent,
  ] = useState("");

  const [documentSource, setDocumentSource] =
    useState("manual");

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

  const [ingestError, setIngestError] =
    useState("");

  /*
   * File ingestion
   */
  const [uploadFile, setUploadFile] =
    useState<File | null>(null);

  const [uploadTitle, setUploadTitle] =
    useState("");

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

  const [uploadError, setUploadError] =
    useState("");

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

      const body = await response.json();

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

      const body = await response.json();

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

      const body = await response.json();

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
      const formData = new FormData();

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

      const body = await response.json();

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
            />

            <SidebarItem
              href="/knowledge"
              icon={BrainCircuit}
              label="Knowledge / RAG"
              active
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
                Vector search active
              </span>
            </div>

            <p className="mt-2 text-xs leading-5 text-slate-500">
              OpenAI embeddings +
              PostgreSQL pgvector
            </p>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="border-b border-slate-800 bg-[#090e18]/80 px-6 py-5 xl:px-10">
            <div>
              <p className="text-sm font-medium text-cyan-400">
                Retrieval-Augmented Generation
              </p>

              <h2 className="mt-1 text-2xl font-semibold">
                Knowledge / RAG
              </h2>

              <p className="mt-1 text-sm text-slate-500">
                Query the live vector knowledge
                base, inspect retrieval evidence
                and ingest new policy documents.
              </p>
            </div>
          </header>

          <div className="p-6 xl:p-10">
            <section className="mb-6 grid gap-4 md:grid-cols-3">
              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <div className="flex items-center gap-3">
                  <div className="rounded-xl bg-cyan-400/10 p-3">
                    <Database className="h-5 w-5 text-cyan-400" />
                  </div>

                  <div>
                    <p className="text-sm text-slate-500">
                      Vector Store
                    </p>

                    <p className="mt-1 font-semibold">
                      PostgreSQL + pgvector
                    </p>
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <div className="flex items-center gap-3">
                  <div className="rounded-xl bg-emerald-400/10 p-3">
                    <BrainCircuit className="h-5 w-5 text-emerald-400" />
                  </div>

                  <div>
                    <p className="text-sm text-slate-500">
                      Retrieval
                    </p>

                    <p className="mt-1 font-semibold">
                      Semantic similarity
                    </p>
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <div className="flex items-center gap-3">
                  <div className="rounded-xl bg-amber-400/10 p-3">
                    <ShieldCheck className="h-5 w-5 text-amber-400" />
                  </div>

                  <div>
                    <p className="text-sm text-slate-500">
                      Generation
                    </p>

                    <p className="mt-1 font-semibold">
                      Grounded + cited
                    </p>
                  </div>
                </div>
              </div>
            </section>

            <div className="mb-6 flex flex-wrap gap-2 rounded-xl border border-slate-800 bg-slate-900/60 p-2">
              <button
                onClick={() => setTab("rag")}
                className={`rounded-lg px-4 py-2 text-sm transition ${
                  tab === "rag"
                    ? "bg-cyan-400 text-slate-950"
                    : "text-slate-400 hover:bg-slate-800 hover:text-white"
                }`}
              >
                RAG Playground
              </button>

              <button
                onClick={() =>
                  setTab("search")
                }
                className={`rounded-lg px-4 py-2 text-sm transition ${
                  tab === "search"
                    ? "bg-cyan-400 text-slate-950"
                    : "text-slate-400 hover:bg-slate-800 hover:text-white"
                }`}
              >
                Semantic Search
              </button>

              <button
                onClick={() =>
                  setTab("ingest")
                }
                className={`rounded-lg px-4 py-2 text-sm transition ${
                  tab === "ingest"
                    ? "bg-cyan-400 text-slate-950"
                    : "text-slate-400 hover:bg-slate-800 hover:text-white"
                }`}
              >
                Ingest Knowledge
              </button>
            </div>

            {tab === "rag" && (
              <div className="grid gap-6 xl:grid-cols-[420px_minmax(0,1fr)]">
                <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                  <div className="flex items-center gap-2">
                    <Sparkles className="h-5 w-5 text-cyan-400" />

                    <h3 className="font-semibold">
                      Ask Knowledge Base
                    </h3>
                  </div>

                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    Ask a customer-support
                    policy question. The backend
                    retrieves relevant pgvector
                    chunks before generating a
                    grounded answer.
                  </p>

                  <form
                    onSubmit={askKnowledge}
                    className="mt-6"
                  >
                    <textarea
                      value={question}
                      onChange={(event) =>
                        setQuestion(
                          event.target.value,
                        )
                      }
                      rows={7}
                      maxLength={2000}
                      className="w-full resize-none rounded-xl border border-slate-700 bg-slate-950 p-4 text-sm leading-6 outline-none transition placeholder:text-slate-600 focus:border-cyan-500"
                      placeholder="Ask a policy question..."
                    />

                    <div className="mt-4">
                      <label className="text-xs text-slate-500">
                        Maximum retrieval
                        candidates
                      </label>

                      <select
                        value={topK}
                        onChange={(event) =>
                          setTopK(
                            Number(
                              event.target.value,
                            ),
                          )
                        }
                        className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-2.5 text-sm outline-none focus:border-cyan-500"
                      >
                        {[1, 2, 3, 4, 5, 6, 8, 10].map(
                          (value) => (
                            <option
                              key={value}
                              value={value}
                            >
                              Top {value}
                            </option>
                          ),
                        )}
                      </select>
                    </div>

                    <button
                      type="submit"
                      disabled={
                        answerLoading ||
                        question.trim().length <
                          2
                      }
                      className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-400 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:opacity-50"
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

                  <div className="mt-6 border-t border-slate-800 pt-5">
                    <p className="text-xs uppercase tracking-wider text-slate-600">
                      Example questions
                    </p>

                    <div className="mt-3 space-y-2">
                      {[
                        "How long does a withdrawal normally take?",
                        "What happens if a deposit is missing?",
                        "What are the identity verification requirements?",
                        "What is the company vacation policy?",
                      ].map((example) => (
                        <button
                          key={example}
                          onClick={() =>
                            setQuestion(example)
                          }
                          className="block w-full rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2.5 text-left text-xs leading-5 text-slate-400 transition hover:border-slate-700 hover:text-white"
                        >
                          {example}
                        </button>
                      ))}
                    </div>
                  </div>
                </section>

                <section className="min-w-0">
                  {answerError && (
                    <div className="flex items-center gap-3 rounded-2xl border border-red-900/50 bg-red-950/20 p-5 text-sm text-red-300">
                      <XCircle className="h-5 w-5" />

                      {answerError}
                    </div>
                  )}

                  {answerLoading && (
                    <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-cyan-900/40 bg-cyan-950/10">
                      <div className="text-center">
                        <LoaderCircle className="mx-auto h-8 w-8 animate-spin text-cyan-400" />

                        <p className="mt-4 font-medium">
                          Running RAG pipeline
                        </p>

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
                      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-slate-800 bg-slate-900/30">
                        <div className="max-w-md text-center">
                          <BrainCircuit className="mx-auto h-10 w-10 text-slate-600" />

                          <p className="mt-4 font-medium text-slate-300">
                            RAG Playground
                          </p>

                          <p className="mt-2 text-sm leading-6 text-slate-500">
                            Ask a question to inspect
                            the generated answer and
                            the exact knowledge chunks
                            used as evidence.
                          </p>
                        </div>
                      </div>
                    )}

                  {answer && !answerLoading && (
                    <div className="space-y-6">
                      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                          <div className="flex items-center gap-2">
                            <Bot className="h-5 w-5 text-cyan-400" />

                            <h3 className="font-semibold">
                              Grounded Answer
                            </h3>
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

                        <p className="mt-5 whitespace-pre-wrap text-sm leading-7 text-slate-300">
                          {answer.answer}
                        </p>

                        <div className="mt-6 grid gap-3 sm:grid-cols-3">
                          <div className="rounded-xl bg-slate-950/60 p-4">
                            <p className="text-xs text-slate-500">
                              Retrieved sources
                            </p>

                            <p className="mt-2 text-xl font-semibold">
                              {
                                answer.retrieval_count
                              }
                            </p>
                          </div>

                          <div className="rounded-xl bg-slate-950/60 p-4">
                            <p className="text-xs text-slate-500">
                              Best similarity
                            </p>

                            <p className="mt-2 text-xl font-semibold">
                              {answer.best_similarity !==
                              null
                                ? `${(
                                    answer.best_similarity *
                                    100
                                  ).toFixed(1)}%`
                                : "—"}
                            </p>
                          </div>

                          <div className="rounded-xl bg-slate-950/60 p-4">
                            <p className="text-xs text-slate-500">
                              Grounding
                            </p>

                            <p
                              className={`mt-2 text-xl font-semibold ${
                                answer.grounded
                                  ? "text-emerald-400"
                                  : "text-amber-400"
                              }`}
                            >
                              {answer.grounded
                                ? "Verified"
                                : "Guarded"}
                            </p>
                          </div>
                        </div>

                        <div className="mt-5 rounded-lg border border-slate-800 bg-slate-950/40 px-4 py-3">
                          <p className="text-xs text-slate-600">
                            Request ID
                          </p>

                          <p className="mt-1 break-all font-mono text-xs text-slate-400">
                            {answer.request_id}
                          </p>
                        </div>
                      </div>

                      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                        <div className="flex items-center justify-between gap-4">
                          <div>
                            <div className="flex items-center gap-2">
                              <Database className="h-5 w-5 text-cyan-400" />

                              <h3 className="font-semibold">
                                Retrieved Evidence
                              </h3>
                            </div>

                            <p className="mt-1 text-sm text-slate-500">
                              Knowledge chunks supplied
                              to the generation layer.
                            </p>
                          </div>

                          <Badge variant="info">
                            {answer.sources.length}{" "}
                            sources
                          </Badge>
                        </div>

                        {answer.sources.length ===
                        0 ? (
                          <div className="mt-5 rounded-xl border border-amber-900/30 bg-amber-950/10 p-5">
                            <p className="text-sm font-medium text-amber-300">
                              No relevant policy
                              evidence found
                            </p>

                            <p className="mt-2 text-sm leading-6 text-slate-500">
                              CXOps should refuse to
                              invent company policy
                              when the knowledge base
                              cannot support the
                              answer.
                            </p>
                          </div>
                        ) : (
                          <div className="mt-5 space-y-4">
                            {answer.sources.map(
                              (source) => (
                                <div
                                  key={`${source.source_id}-${source.chunk_id}`}
                                  className="rounded-xl border border-slate-800 bg-slate-950/50 p-5"
                                >
                                  <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                                    <div>
                                      <div className="flex items-center gap-2">
                                        <Badge variant="info">
                                          {
                                            source.source_id
                                          }
                                        </Badge>

                                        <p className="font-medium text-slate-200">
                                          {
                                            source.title
                                          }
                                        </p>
                                      </div>

                                      <p className="mt-2 text-xs text-slate-600">
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
                    </div>
                  )}
                </section>
              </div>
            )}

            {tab === "search" && (
              <div className="grid gap-6 xl:grid-cols-[420px_minmax(0,1fr)]">
                <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                  <div className="flex items-center gap-2">
                    <Search className="h-5 w-5 text-cyan-400" />

                    <h3 className="font-semibold">
                      Vector Search
                    </h3>
                  </div>

                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    Inspect raw semantic retrieval
                    independently from the LLM
                    generation layer.
                  </p>

                  <form
                    onSubmit={runSearch}
                    className="mt-6"
                  >
                    <textarea
                      value={searchQuery}
                      onChange={(event) =>
                        setSearchQuery(
                          event.target.value,
                        )
                      }
                      rows={5}
                      className="w-full resize-none rounded-xl border border-slate-700 bg-slate-950 p-4 text-sm outline-none focus:border-cyan-500"
                    />

                    <label className="mt-4 block text-xs text-slate-500">
                      Result limit
                    </label>

                    <select
                      value={searchLimit}
                      onChange={(event) =>
                        setSearchLimit(
                          Number(
                            event.target.value,
                          ),
                        )
                      }
                      className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-2.5 text-sm outline-none"
                    >
                      {[1, 3, 5, 10, 20].map(
                        (value) => (
                          <option
                            key={value}
                            value={value}
                          >
                            {value} results
                          </option>
                        ),
                      )}
                    </select>

                    <button
                      disabled={searchLoading}
                      className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-400 px-5 py-3 text-sm font-semibold text-slate-950 disabled:opacity-50"
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
                    <div className="mb-5 rounded-xl border border-red-900/40 bg-red-950/20 p-4 text-sm text-red-300">
                      {searchError}
                    </div>
                  )}

                  <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="font-semibold">
                          Semantic Results
                        </h3>

                        <p className="mt-1 text-sm text-slate-500">
                          Ranked directly by vector
                          similarity.
                        </p>
                      </div>

                      <Badge>
                        {
                          searchResults.length
                        }{" "}
                        results
                      </Badge>
                    </div>

                    {searchLoading ? (
                      <div className="flex h-60 items-center justify-center">
                        <LoaderCircle className="h-7 w-7 animate-spin text-cyan-400" />
                      </div>
                    ) : searchResults.length ===
                      0 ? (
                      <div className="mt-6 rounded-xl border border-dashed border-slate-800 p-12 text-center text-sm text-slate-500">
                        Run a semantic search to
                        inspect pgvector results.
                      </div>
                    ) : (
                      <div className="mt-6 space-y-4">
                        {searchResults.map(
                          (result, index) => (
                            <div
                              key={
                                result.chunk_id
                              }
                              className="rounded-xl border border-slate-800 bg-slate-950/50 p-5"
                            >
                              <div className="flex flex-col justify-between gap-3 sm:flex-row">
                                <div>
                                  <p className="font-medium">
                                    #
                                    {index +
                                      1}{" "}
                                    {metadataTitle(
                                      result.metadata,
                                    ) &&
                                      `— ${metadataTitle(
                                        result.metadata,
                                      )}`}
                                  </p>

                                  <p className="mt-1 text-xs text-slate-600">
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

                                <div className="flex gap-2">
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

                              <p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-slate-400">
                                {
                                  result.content
                                }
                              </p>
                            </div>
                          ),
                        )}
                      </div>
                    )}
                  </div>
                </section>
              </div>
            )}

            {tab === "ingest" && (
              <div className="grid gap-6 xl:grid-cols-2">
                <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                  <div className="flex items-center gap-2">
                    <FileText className="h-5 w-5 text-cyan-400" />

                    <h3 className="font-semibold">
                      Manual Knowledge Ingestion
                    </h3>
                  </div>

                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    Add a policy or operating
                    procedure directly to the RAG
                    knowledge base.
                  </p>

                  <form
                    onSubmit={ingestDocument}
                    className="mt-6 space-y-4"
                  >
                    <div>
                      <label className="text-xs text-slate-500">
                        Document title
                      </label>

                      <input
                        value={documentTitle}
                        onChange={(event) =>
                          setDocumentTitle(
                            event.target.value,
                          )
                        }
                        placeholder="Example: VIP Support Policy"
                        className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none focus:border-cyan-500"
                      />
                    </div>

                    <div>
                      <label className="text-xs text-slate-500">
                        Source
                      </label>

                      <input
                        value={documentSource}
                        onChange={(event) =>
                          setDocumentSource(
                            event.target.value,
                          )
                        }
                        className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none focus:border-cyan-500"
                      />
                    </div>

                    <div>
                      <label className="text-xs text-slate-500">
                        Content
                      </label>

                      <textarea
                        value={documentContent}
                        onChange={(event) =>
                          setDocumentContent(
                            event.target.value,
                          )
                        }
                        rows={12}
                        placeholder="Paste policy content..."
                        className="mt-2 w-full resize-none rounded-xl border border-slate-700 bg-slate-950 p-4 text-sm leading-6 outline-none focus:border-cyan-500"
                      />
                    </div>

                    <button
                      disabled={ingestLoading}
                      className="flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-400 px-5 py-3 text-sm font-semibold text-slate-950 disabled:opacity-50"
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
                    <div className="mt-5 rounded-xl border border-red-900/40 bg-red-950/20 p-4 text-sm text-red-300">
                      {ingestError}
                    </div>
                  )}

                  {ingestResult && (
                    <div className="mt-5 rounded-xl border border-emerald-900/40 bg-emerald-950/20 p-5">
                      <div className="flex items-center gap-2">
                        <CheckCircle2 className="h-5 w-5 text-emerald-400" />

                        <p className="font-medium text-emerald-300">
                          {ingestResult.duplicate
                            ? "Document already exists"
                            : "Knowledge ingested"}
                        </p>
                      </div>

                      <div className="mt-4 space-y-2 text-sm text-slate-400">
                        <p>
                          Document ID:{" "}
                          {
                            ingestResult.document_id
                          }
                        </p>

                        <p>
                          Title:{" "}
                          {ingestResult.title}
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

                <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
                  <div className="flex items-center gap-2">
                    <Upload className="h-5 w-5 text-cyan-400" />

                    <h3 className="font-semibold">
                      Upload Knowledge Document
                    </h3>
                  </div>

                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    Upload a PDF, TXT or Markdown
                    document. CXOps extracts the
                    text, chunks it, embeds it and
                    stores the vectors.
                  </p>

                  <form
                    onSubmit={uploadDocument}
                    className="mt-6"
                  >
                    <label className="block rounded-2xl border border-dashed border-slate-700 bg-slate-950/40 p-10 text-center transition hover:border-cyan-700">
                      <Upload className="mx-auto h-9 w-9 text-slate-500" />

                      <p className="mt-4 text-sm font-medium text-slate-300">
                        {uploadFile
                          ? uploadFile.name
                          : "Choose a knowledge document"}
                      </p>

                      <p className="mt-2 text-xs text-slate-600">
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
                      <label className="text-xs text-slate-500">
                        Optional title
                      </label>

                      <input
                        value={uploadTitle}
                        onChange={(event) =>
                          setUploadTitle(
                            event.target.value,
                          )
                        }
                        placeholder="Defaults to filename"
                        className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none focus:border-cyan-500"
                      />
                    </div>

                    <button
                      disabled={
                        uploadLoading ||
                        !uploadFile
                      }
                      className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-400 px-5 py-3 text-sm font-semibold text-slate-950 disabled:opacity-50"
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
                    <div className="mt-5 rounded-xl border border-red-900/40 bg-red-950/20 p-4 text-sm text-red-300">
                      {uploadError}
                    </div>
                  )}

                  {uploadResult && (
                    <div className="mt-5 rounded-xl border border-emerald-900/40 bg-emerald-950/20 p-5">
                      <div className="flex items-center gap-2">
                        <CheckCircle2 className="h-5 w-5 text-emerald-400" />

                        <p className="font-medium text-emerald-300">
                          {uploadResult.duplicate
                            ? "Document already exists"
                            : "Document embedded successfully"}
                        </p>
                      </div>

                      <div className="mt-4 space-y-2 text-sm text-slate-400">
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

                  <div className="mt-6 rounded-xl border border-cyan-900/30 bg-cyan-950/10 p-5">
                    <p className="text-sm font-medium text-cyan-300">
                      Ingestion pipeline
                    </p>

                    <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                      <span className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
                        Parse
                      </span>

                      <span>→</span>

                      <span className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
                        Chunk
                      </span>

                      <span>→</span>

                      <span className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
                        Embed
                      </span>

                      <span>→</span>

                      <span className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
                        pgvector
                      </span>
                    </div>
                  </div>
                </section>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}