"use client";

import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileText,
  LockKeyhole,
  ShieldCheck,
  TriangleAlert,
  UploadCloud,
} from "lucide-react";
import Link from "next/link";
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
} from "react";

import InsufficientPermission from "@/components/insufficient-permission";
import { CAPABILITIES } from "@/lib/authorization/capabilities";
import { useAuthorization } from "@/lib/authorization/context";
import { mayUseClaimReview } from "@/lib/claim-review/access";
import {
  reviewCore,
  reviewForOrganization,
  validSyntheticReviewUpload,
  type ReviewRole,
  type ScopedReview,
} from "@/lib/claim-review/index";

const FIELD =
  "min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-100";
const LABEL = "mb-2 block text-xs font-semibold text-slate-600";

export default function ClaimReviewWorkspace({
  authorizedOrganizationId,
}: {
  authorizedOrganizationId: number;
}) {
  const { organizationId, capabilities, can } = useAuthorization();
  const isAuthorized =
    organizationId === authorizedOrganizationId &&
    mayUseClaimReview(
      { organization_id: organizationId, capabilities },
      authorizedOrganizationId,
    ) &&
    can(CAPABILITIES.EVALUATION_MANAGE);

  const [role, setRole] = useState<ReviewRole>(1);
  const [reviewerId, setReviewerId] = useState("");
  const [review, setReview] = useState<ScopedReview | null>(null);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [dirty, setDirty] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const tenantRef = useRef(organizationId);

  useEffect(() => {
    tenantRef.current = organizationId;
  }, [organizationId]);

  useEffect(() => {
    if (!dirty) return;
    const preventUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventUnload);
    return () => window.removeEventListener("beforeunload", preventUnload);
  }, [dirty]);

  const scoped = reviewForOrganization(review, organizationId);
  const active = scoped?.rows[selected] ?? null;
  const progress = scoped
    ? reviewCore.progress(scoped.rows, scoped.reviewerRole, scoped.reviewerId)
    : null;
  const status = active && scoped
    ? reviewCore.reviewStatus(active, scoped.reviewerRole, scoped.reviewerId)
    : null;

  const loadSheet = async (event: ChangeEvent<HTMLInputElement>) => {
    if (!isAuthorized || scoped) return;
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    setError("");
    setMessage("");
    const requestOrganizationId = organizationId;
    try {
      if (!validSyntheticReviewUpload(file)) {
        throw new Error("Choose a fictional C6 CSV no larger than 12 MB.");
      }
      const source = await file.text();
      if (tenantRef.current !== requestOrganizationId) return;
      const rows = reviewCore.validateWorksheet(source, role, reviewerId.trim());
      setReview({
        organizationId: requestOrganizationId,
        reviewerRole: role,
        reviewerId: reviewerId.trim(),
        rows,
      });
      setSelected(0);
      setDirty(false);
      setMessage(
        "Loaded " + rows.length + " fictional claims. All edits stay on your device.",
      );
    } catch (failure) {
      if (inputRef.current) inputRef.current.value = "";
      setError(failure instanceof Error ? failure.message : "Could not open CSV.");
    }
  };

  const edit = (change: Partial<{
    label: string;
    evidence: string;
    note: string;
  }>) => {
    if (!isAuthorized || !scoped || !active) return;
    try {
      const rows = reviewCore.editRow(
        scoped.rows,
        selected,
        scoped.reviewerRole,
        scoped.reviewerId,
        change,
      );
      setReview({ ...scoped, rows });
      setDirty(true);
      setError("");
      setMessage("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Review refused.");
    }
  };

  const quoteSelection = () => {
    if (!isAuthorized || !active) return;
    const quote = window.getSelection()?.toString().trim() ?? "";
    if (
      quote.length < 8 ||
      quote.length > 1000 ||
      !reviewCore.sourceBodies(active.cited_source_passages).some(
        (source) => source.includes(quote),
      )
    ) {
      setError("Select 8–1,000 exact characters in a cited passage.");
      return;
    }
    edit({ evidence: quote });
  };

  const download = () => {
    if (!isAuthorized || !scoped || !progress) return;
    try {
      const output = reviewCore.exportReviewerCsv(
        scoped.rows,
        scoped.reviewerRole,
        scoped.reviewerId,
        { allowIncomplete: !progress.complete },
      );
      const url = URL.createObjectURL(
        new Blob(["\ufeff", output], { type: "text/csv;charset=utf-8" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download =
        "cxops-reviewer-" +
        (scoped.reviewerRole === 1 ? "A" : "B") +
        (progress.complete ? "-completed.csv" : "-draft.csv");
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 2000);
      setDirty(false);
      setMessage(
        progress.complete
          ? "Review CSV exported. Python C6 validation and separate human adjudication are still required."
          : "Draft exported. Incomplete claims cannot count as an approved review.",
      );
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "CSV export failed.");
    }
  };

  // The server rechecks the backend's capabilities and tenant before sending
  // any reviewer UI. This client check additionally guards stale tenant state.
  if (!isAuthorized) return <InsufficientPermission />;

  const prefix = scoped?.reviewerRole === 2 ? "reviewer_2_" : "reviewer_1_";

  return (
    <div className="min-h-screen bg-[#f8f9ff]">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200 bg-white/90 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1440px] items-center justify-between gap-4 px-5 lg:px-10">
            <div>
              <p className="text-sm font-semibold text-slate-900">Claim Review Studio</p>
              <p className="text-[11px] text-slate-500">Staff · Synthetic local review</p>
            </div>
            <Link
              href="/evaluations"
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700 hover:text-violet-600"
            >
              <ArrowLeft className="h-4 w-4" /> Evaluations
            </Link>
          </div>
        </header>

        <main className="mx-auto max-w-[1440px] space-y-6 px-5 pb-24 pt-[110px] lg:px-10">
          <section className="rounded-[24px] border border-violet-100 bg-gradient-to-br from-white to-violet-50 p-7 shadow-sm md:p-9">
            <p className="inline-flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-violet-700">
              <ShieldCheck className="h-4 w-4" /> Staff evaluation managers only
              <span className="rounded-full bg-violet-100 px-2 py-1">SOURCE ONLY · RELEASE HOLD</span>
            </p>
            <h1 className="mt-5 max-w-3xl text-3xl font-semibold tracking-[-0.05em] text-slate-950 sm:text-4xl">
              Judge the claim. Read the source. Keep the evidence.
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-600">
              Privately review fictional chatbot claims against their cited passages.
              No customer record is loaded, no AI model is called, and no review
              is saved to CXOps databases or treated as a release approval.
            </p>
          </section>

          {!scoped ? (
            <section className="grid gap-7 rounded-[22px] border border-slate-200 bg-white p-6 shadow-sm lg:grid-cols-2 lg:p-8">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-violet-700">
                  01 / Open a private review worksheet
                </p>
                <h2 className="mt-4 text-xl font-semibold text-slate-900">
                  One reviewer, one private CSV
                </h2>
                <p className="mt-3 text-sm leading-7 text-slate-600">
                  Start with a C6-generated blank CSV containing fictional claims.
                  Reviewer A and B must work on separate copies without seeing one
                  another&apos;s judgments before independent adjudication.
                </p>
                <p className="mt-5 flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-6 text-amber-900">
                  <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0" />
                  Reviewer names are self-attested. Logging in does not prove
                  independence, certify factual accuracy, or authorize a deployment.
                </p>
              </div>
              <div className="space-y-4">
                <label className="block">
                  <span className={LABEL}>Reviewer assignment</span>
                  <select
                    className={FIELD}
                    value={role}
                    onChange={(event) => setRole(Number(event.target.value) as ReviewRole)}
                  >
                    <option value={1}>Reviewer A — independent first review</option>
                    <option value={2}>Reviewer B — independent second review</option>
                  </select>
                </label>
                <label className="block">
                  <span className={LABEL}>Reviewer identifier · Self-attested</span>
                  <input
                    className={FIELD}
                    maxLength={80}
                    spellCheck={false}
                    autoComplete="off"
                    placeholder="Your private reviewer ID"
                    value={reviewerId}
                    onChange={(event) => setReviewerId(event.target.value)}
                  />
                </label>
                <label className="flex min-h-28 cursor-pointer items-center gap-4 rounded-2xl border border-dashed border-violet-300 bg-violet-50/60 p-5 focus-within:ring-2 focus-within:ring-violet-300">
                  <UploadCloud className="h-8 w-8 shrink-0 text-violet-600" />
                  <span className="text-sm font-semibold text-slate-800">
                    Choose synthetic C6 CSV
                    <span className="mt-1 block text-xs font-normal text-slate-500">
                      Local .csv · 12 MB maximum · No upload
                    </span>
                  </span>
                  <input
                    ref={inputRef}
                    type="file"
                    accept=".csv,text/csv"
                    aria-label="Choose private synthetic C6 worksheet"
                    className="sr-only"
                    onChange={(event) => void loadSheet(event)}
                  />
                </label>
              </div>
            </section>
          ) : (
            <>
              <section className="flex flex-wrap items-center justify-between gap-5 rounded-[22px] border border-slate-200 bg-white p-5 shadow-sm">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wide text-violet-700">
                    {scoped.reviewerRole === 1 ? "Reviewer A" : "Reviewer B"} · Local synthetic preview
                  </p>
                  <h2 className="mt-2 text-lg font-semibold text-slate-950">
                    {reviewCore.EXPECTED_DATASET}
                  </h2>
                  <p className="mt-1 text-xs text-slate-500">
                    Source SHA-256 fingerprints are authenticated by the Python C6 scorer, not this page.
                  </p>
                </div>
                <div className="w-full max-w-[240px]">
                  <div className="flex justify-between text-xs text-slate-500">
                    <span>Completed</span>
                    <strong>{progress?.finished ?? 0} of {progress?.total ?? 0}</strong>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-violet-600"
                      style={{
                        width: String(
                          progress?.total
                            ? Math.round(100 * progress.finished / progress.total)
                            : 0
                        ) + "%",
                      }}
                    />
                  </div>
                </div>
              </section>

              <div className="grid gap-5 lg:grid-cols-[minmax(230px,0.32fr)_minmax(0,1fr)]">
                <nav
                  aria-label="Claims to review"
                  className="self-start overflow-hidden rounded-[22px] border border-slate-200 bg-white shadow-sm"
                >
                  <p className="border-b border-slate-100 px-5 py-4 text-xs font-bold uppercase tracking-[0.13em] text-slate-500">
                    Claim queue
                  </p>
                  <div className="max-h-[640px] overflow-auto">
                    {scoped.rows.map((entry, index) => {
                      const complete = reviewCore.reviewStatus(
                        entry, scoped.reviewerRole, scoped.reviewerId,
                      ).complete;
                      return (
                        <button
                          key={entry.review_key}
                          type="button"
                          onClick={() => setSelected(index)}
                          aria-current={selected === index ? "step" : undefined}
                          className={
                            "flex w-full items-start gap-3 border-b border-slate-100 px-4 py-4 text-left text-xs " +
                            (selected === index
                              ? "border-l-[3px] border-l-violet-600 bg-violet-50"
                              : "border-l-[3px] border-l-transparent hover:bg-slate-50")
                          }
                        >
                          <span className="font-mono text-slate-400">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span dir="auto" className="block break-words font-medium text-slate-800">
                              {entry.claim_text}
                            </span>
                            <span className="mt-2 block break-all font-mono text-[10px] text-slate-400">
                              {entry.review_key}
                            </span>
                          </span>
                          <span className={complete ? "text-emerald-600" : "text-slate-300"}>
                            {complete ? "✓" : "○"}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </nav>

                {active && status && (
                  <div className="space-y-5">
                    <section className="rounded-[22px] border border-slate-200 bg-white p-6 shadow-sm">
                      <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-violet-700">
                        02 / Evaluate individual claim
                      </p>
                      <h2 dir="auto" className="mt-4 break-words text-2xl font-semibold text-slate-950">
                        {active.claim_text}
                      </h2>
                      <div className="mt-5 border-l-2 border-violet-200 pl-4">
                        <p className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
                          Complete generated answer
                        </p>
                        <p dir="auto" className="mt-2 break-words text-sm leading-7 text-slate-600">
                          {active.answer_text}
                        </p>
                      </div>
                      <div className="mt-6 overflow-hidden rounded-2xl border border-teal-200 bg-teal-50/50">
                        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-teal-200 px-4 py-3">
                          <span className="inline-flex items-center gap-2 text-xs font-semibold text-teal-800">
                            <FileText className="h-4 w-4" /> Cited source · Frozen revision
                          </span>
                          <button
                            type="button"
                            onClick={quoteSelection}
                            className="rounded-lg px-2 py-1 text-xs font-semibold text-teal-700 hover:bg-teal-100"
                          >
                            Use selected text ↗
                          </button>
                        </div>
                        <pre
                          dir="auto"
                          tabIndex={0}
                          className="max-h-72 overflow-auto whitespace-pre-wrap break-words px-5 py-4 text-xs leading-7 text-teal-950"
                        >
                          {active.cited_source_passages}
                        </pre>
                      </div>
                    </section>

                    <section className="rounded-[22px] border border-slate-200 bg-white p-6 shadow-sm">
                      <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-violet-700">
                        03 / Independent assessment
                      </p>
                      <label className="mt-5 block">
                        <span className={LABEL}>My claim label</span>
                        <select
                          className={FIELD}
                          aria-label="Claim review label"
                          value={active[prefix + "label"]}
                          onChange={(event) => edit({ label: event.target.value })}
                        >
                          <option value="">Not reviewed</option>
                          {reviewCore.LABELS.map((item) => (
                            <option key={item} value={item}>
                              {item.charAt(0).toUpperCase() + item.slice(1)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="mt-5 block">
                        <span className={LABEL}>
                          Exact cited excerpt · Required for supported/contradicted
                        </span>
                        <textarea
                          dir="auto"
                          maxLength={1000}
                          className={FIELD + " min-h-28 resize-y"}
                          placeholder="Quote at least 8 exact characters from the cited passage."
                          value={active[prefix + "evidence"]}
                          onChange={(event) => edit({ evidence: event.target.value })}
                        />
                      </label>
                      <label className="mt-5 block">
                        <span className={LABEL}>
                          Written rationale · Required for unsupported/unclear
                        </span>
                        <textarea
                          dir="auto"
                          maxLength={500}
                          className={FIELD + " min-h-28 resize-y"}
                          placeholder="Explain missing or ambiguous evidence."
                          value={active[prefix + "note"]}
                          onChange={(event) => edit({ note: event.target.value })}
                        />
                      </label>
                      <p
                        aria-live="polite"
                        className={
                          "mt-4 flex items-center gap-2 text-xs " +
                          (status.complete ? "text-emerald-700" : "text-amber-700")
                        }
                      >
                        {status.complete
                          ? <CheckCircle2 className="h-4 w-4" />
                          : <TriangleAlert className="h-4 w-4" />}
                        {status.complete
                          ? "Judgment fields are filled; not a factual certificate."
                          : status.reason}
                      </p>
                      <div className="mt-5 flex justify-between gap-3 border-t border-slate-100 pt-5">
                        <button
                          type="button"
                          disabled={selected === 0}
                          onClick={() => setSelected((index) => Math.max(0, index - 1))}
                          className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 px-4 py-2 text-xs font-semibold text-slate-700 disabled:opacity-40"
                        >
                          <ArrowLeft className="h-4 w-4" /> Previous
                        </button>
                        <button
                          type="button"
                          disabled={selected === scoped.rows.length - 1}
                          onClick={() => setSelected((index) => Math.min(
                            scoped.rows.length - 1, index + 1,
                          ))}
                          className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40"
                        >
                          Next <ArrowRight className="h-4 w-4" />
                        </button>
                      </div>
                    </section>
                  </div>
                )}
              </div>

              <section className="flex flex-wrap items-center justify-between gap-6 rounded-[22px] border border-violet-200 bg-violet-50/70 p-6">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wide text-violet-700">
                    04 / Private file handoff
                  </p>
                  <h2 className="mt-2 text-xl font-semibold text-slate-950">
                    {progress?.complete ? "Every judgment field is complete." : "Save your private draft."}
                  </h2>
                  <p className="mt-2 max-w-2xl text-xs leading-6 text-slate-600">
                    Python C6 still checks source fingerprints, independent reviewer
                    files and any third-person dispute resolution. Nothing here
                    automatically grants approval or publishes chatbot responses.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={download}
                  className="inline-flex min-h-12 items-center gap-2 rounded-xl bg-violet-600 px-6 py-3 text-sm font-semibold text-white hover:bg-violet-700"
                >
                  <ArrowDownToLine className="h-4 w-4" />
                  {progress?.complete ? "Download completed CSV" : "Download draft CSV"}
                </button>
              </section>
            </>
          )}

          {(error || message) && (
            <p
              role={error ? "alert" : "status"}
              className={
                "rounded-2xl border p-4 text-sm " +
                (error
                  ? "border-rose-200 bg-rose-50 text-rose-800"
                  : "border-emerald-200 bg-emerald-50 text-emerald-800")
              }
            >
              {error || message}
            </p>
          )}

          <p className="text-center text-[11px] leading-6 text-slate-500">
            No customer data, external AI model, reviewer approval, database
            write or network upload · Synthetic QA only.
          </p>
        </main>
      </div>
    </div>
  );
}
