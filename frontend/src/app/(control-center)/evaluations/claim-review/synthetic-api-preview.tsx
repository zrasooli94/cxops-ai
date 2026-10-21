"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";

import { useAuthorization } from "@/lib/authorization/context";
import { reviewApi, type Queue, type ReviewLabel } from "@/lib/claim-review/api";
import { reviewCore, type ReviewRow } from "@/lib/claim-review/index";

/**
 * C12 development-only API pilot. Not mounted at all unless the authenticated
 * server passes the explicit preview flag. The C11 backend independently
 * requires development+opt-in+JWT+tenant RBAC. No client-controlled actor,
 * org selector, session token, release approval, or business action.
 *
 * Source text is NEVER sent to the API: it stays in memory from a local C6
 * synthetic worksheet. Only an exact source-fingerprint-matched claim can
 * submit its own label and bounded evidence/rationale.
 */
export default function SyntheticApiPreview({ organizationId }: { organizationId: number }) {
  const auth = useAuthorization();
  const [batchId, setBatchId] = useState("");
  const [first, setFirst] = useState("");
  const [second, setSecond] = useState("");
  const [queue, setQueue] = useState<Queue | null>(null);
  const [localRows, setLocalRows] = useState<ReviewRow[]>([]);
  const [selected, setSelected] = useState("");
  const [label, setLabel] = useState<ReviewLabel>("unclear");
  const [evidence, setEvidence] = useState("");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const scope = useRef(organizationId);
  const sequence = useRef(0);

  useEffect(() => {
    scope.current = organizationId;
    sequence.current += 1;
  }, [organizationId]);

  const permitted = auth.organizationId === organizationId &&
    auth.canAll(["evaluation.read", "evaluation.manage"]);
  const administrator = permitted && auth.can("member.manage");
  const id = Number(batchId);
  const validBatchId = Number.isSafeInteger(id) && id > 0;
  const entry = queue?.items.find((item) => item.review_key === selected);
  const local = localRows.find((row) =>
    row.review_key === selected && row.fingerprint === entry?.fingerprint
  );

  const act = async (task: () => Promise<string | Queue>, name: string) => {
    if (!permitted || busy) return;
    const orgAtStart = organizationId;
    const requestSequence = ++sequence.current;
    setBusy(true);
    setError("");
    setResult("");
    try {
      const response = await task();
      if (scope.current !== orgAtStart || sequence.current !== requestSequence) return;
      if (typeof response === "string") {
        setResult(response);
      } else {
        setQueue(response);
        setResult("Only your own claim progress is visible.");
      }
    } catch (failure) {
      if (scope.current !== orgAtStart || sequence.current !== requestSequence) return;
      setError(failure instanceof Error ? failure.message : name + " failed.");
      // If a role or membership changed, the server has final authority.
      if (failure && typeof failure === "object" && "status" in failure &&
          (failure.status === 401 || failure.status === 403)) auth.refresh();
    } finally {
      if (scope.current === orgAtStart && sequence.current === requestSequence) {
        setBusy(false);
      }
    }
  };

  const loadLocal = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file || !permitted) return;
    if (file.size <= 0 || file.size > 12_000_000 || !file.name.endsWith(".csv")) {
      setError("Only a bounded fictional C6 CSV is accepted.");
      return;
    }
    const orgAtStart = organizationId;
    const seq = ++sequence.current;
    try {
      // The browser validates CSV structure, not source SHA authenticity;
      // C10 backend independently fixes the canonical source fingerprints.
      const parsed = reviewCore.validateWorksheet(await file.text(), 1, "c12-local-preview");
      if (scope.current !== orgAtStart || sequence.current !== seq) return;
      setLocalRows(parsed);
      setResult("Fictional C6 source file loaded locally. No source text uploaded.");
    } catch {
      if (scope.current === orgAtStart && sequence.current === seq) {
        setError("C6 synthetic CSV invalid or unsupported.");
      }
    }
  };

  const persist = () => {
    if (!validBatchId || !entry || !local || entry.completed || !permitted) {
      setError("Select an uncompleted assigned claim matching a local C6 fingerprint.");
      return;
    }
    void act(async () => {
      const response = await reviewApi.submit(
        id, selected, label, evidence, rationale,
      );
      const refreshed = await reviewApi.queue(id);
      setQueue(refreshed);
      setEvidence("");
      setRationale("");
      return "Recorded " + response.event_kind +
        " against an immutable synthetic source. No release approved.";
    }, "Submission");
  };

  if (!permitted) return null;

  return (
    <section aria-label="Development-only synthetic API preview"
      className="space-y-5 rounded-[22px] border border-amber-300 bg-amber-50 p-6">
      <div>
        <p className="text-xs font-bold uppercase tracking-wider text-amber-800">
          C12 · Development-only API preview
        </p>
        <p className="mt-2 text-sm text-amber-950">
          Explicitly opt-in fictional storage. The C11 backend independently
          checks your current identity, tenant and reviewer assignment.
          No customer content, AI calls, or production release decisions.
        </p>
      </div>
      <div className="flex flex-wrap gap-3">
        {administrator && (
          <button type="button" disabled={busy} className="rounded-lg bg-amber-900 px-4 py-2 text-sm text-white"
            onClick={() => void act(async () => {
              const batch = await reviewApi.createBatch();
              setBatchId(String(batch.id));
              return "Fictional batch created: " + batch.id;
            }, "Create batch")}>
            Create fictional batch
          </button>
        )}
        <label className="text-xs font-semibold text-slate-700">
          Batch ID
          <input aria-label="Synthetic batch ID" className="ml-2 max-w-36 rounded border p-2"
            inputMode="numeric" value={batchId} onChange={(e) => {
              setBatchId(e.target.value);
              setQueue(null);
              setSelected("");
            }} />
        </label>
        <button type="button" disabled={busy || !validBatchId}
          className="rounded-lg border border-amber-900 px-4 py-2 text-sm text-amber-950"
          onClick={() => void act(() => reviewApi.queue(id), "Queue")}>
          Load my assigned queue
        </button>
      </div>
      {administrator && (
        <div className="flex flex-wrap items-end gap-3 border-t border-amber-200 pt-4">
          <label className="text-xs">Reviewer A subject
            <input aria-label="Reviewer A subject" className="block rounded border p-2" value={first}
              maxLength={255} onChange={(e) => setFirst(e.target.value)} />
          </label>
          <label className="text-xs">Reviewer B subject
            <input aria-label="Reviewer B subject" className="block rounded border p-2" value={second}
              maxLength={255} onChange={(e) => setSecond(e.target.value)} />
          </label>
          <button type="button" disabled={busy || !validBatchId}
            className="rounded-lg bg-amber-900 px-4 py-2 text-sm text-white"
            onClick={() => void act(async () => {
              const done = await reviewApi.assign(id, first, second);
              return "Assigned " + done.assigned_slots + " fictional reviewer slots.";
            }, "Assignment")}>
            Assign reviewers
          </button>
        </div>
      )}
      <label className="block text-xs font-semibold text-slate-700">
        Local fictional source worksheet (never uploaded)
        <input type="file" accept=".csv,text/csv" aria-label="C6 fictional source CSV"
          className="mt-2 block text-sm" onChange={(event) => void loadLocal(event)} />
      </label>
      {queue && (
        <div className="space-y-3 border-t border-amber-200 pt-4">
          <p className="text-xs text-slate-700">{queue.total} assigned claim fingerprints</p>
          <select aria-label="Assigned claim" value={selected}
            onChange={(event) => {
              setSelected(event.target.value);
              setEvidence("");
              setRationale("");
            }}
            className="w-full rounded border bg-white p-2 text-sm">
            <option value="">Choose an assigned synthetic claim</option>
            {queue.items.map((item) => (
              <option key={item.review_key} value={item.review_key}>
                {item.review_key} {item.completed ? "(completed)" : "(uncompleted)"}
              </option>
            ))}
          </select>
          {entry && (
            <div className="space-y-3">
              <p className="text-xs text-slate-700">
                Local source fingerprint: {local ? "verified against private queue" : "missing or mismatched"}
              </p>
              {local && (
                <>
                  <p dir="auto" className="break-words text-sm font-medium">{local.claim_text}</p>
                  <pre dir="auto" className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-white p-3 text-xs">
                    {local.cited_source_passages}
                  </pre>
                </>
              )}
              <label className="block text-xs">Decision
                <select aria-label="Synthetic review label" value={label}
                  className="ml-2 rounded border p-2"
                  onChange={(event) => setLabel(event.target.value as ReviewLabel)}>
                  <option value="supported">Supported</option>
                  <option value="contradicted">Contradicted</option>
                  <option value="unsupported">Unsupported</option>
                  <option value="unclear">Unclear</option>
                </select>
              </label>
              <label className="block text-xs">Exact cited excerpt
                <textarea aria-label="Synthetic evidence excerpt" value={evidence}
                  maxLength={1000} onChange={(event) => setEvidence(event.target.value)}
                  className="mt-1 block min-h-16 w-full rounded border p-2" />
              </label>
              <label className="block text-xs">Review rationale
                <textarea aria-label="Synthetic review rationale" value={rationale}
                  maxLength={500} onChange={(event) => setRationale(event.target.value)}
                  className="mt-1 block min-h-16 w-full rounded border p-2" />
              </label>
              <p className="text-xs text-amber-900">
                Third-person adjudication remains API-only until a separate
                dispute-only private queue is designed and audited.
              </p>
              <button type="button" disabled={busy || !local || entry.completed}
                className="rounded-lg bg-amber-900 px-4 py-2 text-sm text-white disabled:opacity-50"
                onClick={persist}>
                Record immutable fictional judgment
              </button>
            </div>
          )}
        </div>
      )}
      {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
      {result && <p role="status" className="text-sm text-emerald-800">{result}</p>}
      <p className="text-xs text-amber-900">
        Source SHA-256 authenticity remains the backend&apos;s responsibility.
        The existing private CSV workflow below remains unchanged.
      </p>
    </section>
  );
}
