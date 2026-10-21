"use client";

/**
 * C12 source-only optional development panel. This is NOT a production
 * reviewer client and has no feature-activation or tenant/actor override.
 * Trusted session + selected tenant come from the existing BFF, and FastAPI
 * C11 again checks both membership and capability on every request.
 */
import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  FileCheck2,
  LockKeyhole,
  Plus,
  RefreshCcw,
  ShieldAlert,
  UserRoundCheck,
} from "lucide-react";

import { useAuthorization } from "@/lib/authorization/context";
import type { ReviewRow } from "@/lib/claim-review/index";
import {
  C12ReviewApiError,
  adjudicateSyntheticClaim,
  assignSyntheticReviewers,
  createSyntheticBatch,
  fetchOwnClaimQueue,
  ownQueueMatchesLocalClaim,
  submitOwnClaimReview,
  validReviewLabel,
  type OwnClaimQueue,
  type ReviewInput,
} from "@/lib/claim-review/private-api";

const CONTROL = "min-h-11 w-full rounded-xl border border-violet-200 bg-white px-3 py-2 text-sm text-slate-800 focus-visible:outline-2 focus-visible:outline-violet-500";

export default function SyntheticApiPanel({
  organizationId,
  localClaim,
  localReviewerSlot,
  localJudgmentComplete,
}: {
  organizationId: number;
  localClaim: ReviewRow | null;
  localReviewerSlot: 1 | 2;
  localJudgmentComplete: boolean;
}) {
  const auth = useAuthorization();
  const tenantRef = useRef(organizationId);
  const [batchInput, setBatchInput] = useState("");
  const [assignedA, setAssignedA] = useState("");
  const [assignedB, setAssignedB] = useState("");
  const [queue, setQueue] = useState<OwnClaimQueue | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    tenantRef.current = organizationId;
    // The AuthorizationProvider is keyed by org, but this is also a
    // defense against stale delayed promises during navigation.
  }, [organizationId]);

  const canAdmin = auth.can("member.manage");
  const selectedBatch = Number(batchInput);
  const batchIsValid =
    Number.isSafeInteger(selectedBatch) &&
    selectedBatch > 0 &&
    selectedBatch <= 2147483647;
  const ownedQueue = queue?.batch_id === selectedBatch ? queue : null;
  const matched = !!localClaim && ownQueueMatchesLocalClaim(
    ownedQueue, localClaim.review_key, localClaim.fingerprint,
  );
  const localPrefix = localReviewerSlot === 1 ? "reviewer_1_" : "reviewer_2_";
  const localLabel = localClaim?.[localPrefix + "label"] ?? "";
  const localEvidence = localClaim?.[localPrefix + "evidence"] ?? "";
  const localRationale = localClaim?.[localPrefix + "note"] ?? "";

  const input: ReviewInput | null =
    validReviewLabel(localLabel) && localJudgmentComplete
      ? {
          label: localLabel,
          ...(localLabel === "supported" || localLabel === "contradicted"
            ? { evidence_excerpt: localEvidence }
            : { rationale: localRationale }),
        }
      : null;

  async function execute<T>(
    fn: () => Promise<T>,
    onSuccess: (result: T) => void,
  ) {
    if (busy || tenantRef.current !== auth.organizationId ||
        auth.organizationId !== organizationId) return;
    setBusy(true);
    setError("");
    setMessage("");
    const requestOrganizationId = organizationId;
    try {
      const result = await fn();
      if (tenantRef.current !== requestOrganizationId) return;
      onSuccess(result);
    } catch (failure) {
      if (tenantRef.current !== requestOrganizationId) return;
      setError(
        failure instanceof Error
          ? failure.message
          : "This synthetic reviewer request could not be completed.",
      );
      if (failure instanceof C12ReviewApiError &&
          (failure.status === 401 || failure.status === 403)) {
        auth.refresh(); // server refreshes actual role/tenant snapshot
      }
    } finally {
      if (tenantRef.current === requestOrganizationId) setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="c12-review-preview-title"
      className="rounded-[24px] border-2 border-dashed border-violet-300 bg-violet-50 p-6 shadow-sm"
    >
      <p className="inline-flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.15em] text-violet-700">
        <LockKeyhole className="h-4 w-4" />
        DEV-ONLY PRIVATE API PREVIEW · FICTIONAL C6 DATA
      </p>
      <h2 id="c12-review-preview-title" className="mt-3 text-xl font-semibold text-slate-900">
        Connect your local review to the C11 audit
      </h2>
      <p className="mt-2 max-w-4xl text-xs leading-6 text-violet-900">
        This is a development test workflow only. Real JWT membership and the
        organization-selected backend proxy authorize every request. Only the
        current reviewer&apos;s completion flags are returned, and the database
        stores hashes instead of raw evidence. C11 is disabled in staging and
        production. No review is a release approval.
      </p>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <div className="space-y-4 rounded-2xl border border-violet-100 bg-white p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <FileCheck2 className="h-4 w-4 text-violet-600" />
            Review batch
          </div>
          <label className="block">
            <span className="mb-2 block text-xs text-slate-600">
              Synthetic batch ID (from an authorized owner)
            </span>
            <input
              aria-label="Synthetic claim batch ID"
              className={CONTROL}
              inputMode="numeric"
              value={batchInput}
              placeholder="e.g. 1"
              onChange={(event) => {
                setBatchInput(event.target.value);
                setQueue(null);
              }}
            />
          </label>
          {canAdmin && (
            <button
              type="button"
              disabled={busy}
              className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-violet-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
              onClick={() => void execute(createSyntheticBatch, (batch) => {
                setBatchInput(String(batch.id));
                setQueue(null);
                setMessage(
                  "Fictional review batch created. Only hashes and audit metadata were stored.",
                );
              })}
            >
              <Plus className="h-4 w-4" /> Create fictional batch
            </button>
          )}
          {canAdmin && (
            <div className="space-y-3 border-t border-slate-100 pt-4">
              <p className="text-xs font-semibold text-slate-700">
                Admin: assign two distinct current tenant staff subject IDs
              </p>
              <input
                aria-label="Reviewer A subject ID"
                className={CONTROL}
                maxLength={255}
                value={assignedA}
                placeholder="Reviewer A authenticated subject"
                onChange={(event) => setAssignedA(event.target.value)}
              />
              <input
                aria-label="Reviewer B subject ID"
                className={CONTROL}
                maxLength={255}
                value={assignedB}
                placeholder="Reviewer B authenticated subject"
                onChange={(event) => setAssignedB(event.target.value)}
              />
              <button
                type="button"
                disabled={busy || !batchIsValid || !assignedA || !assignedB || assignedA === assignedB}
                className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-violet-300 bg-white px-4 py-2 text-xs font-semibold text-violet-700 disabled:opacity-40"
                onClick={() => void execute(
                  () => assignSyntheticReviewers(
                    selectedBatch, assignedA.trim(), assignedB.trim(),
                  ),
                  () => {
                    setMessage("Two immutable reviewer slots assigned in the development audit.");
                  },
                )}
              >
                <UserRoundCheck className="h-4 w-4" /> Assign two reviewers
              </button>
            </div>
          )}
        </div>

        <div className="space-y-4 rounded-2xl border border-violet-100 bg-white p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <ShieldAlert className="h-4 w-4 text-violet-600" />
            My private assigned claim queue
          </div>
          <button
            type="button"
            disabled={busy || !batchIsValid}
            className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-violet-300 bg-violet-50 px-4 py-2 text-sm font-semibold text-violet-900 disabled:opacity-40"
            onClick={() => void execute(
              () => fetchOwnClaimQueue(selectedBatch),
              (result) => {
                setQueue(result);
                setMessage(
                  result.total === 0
                    ? "You are not an assigned reviewer in this batch."
                    : "Loaded your own private completion flags. Other reviewer labels are not available.",
                );
              },
            )}
          >
            <RefreshCcw className="h-4 w-4" /> Open / refresh my queue
          </button>
          {ownedQueue && (
            <div aria-live="polite" className="rounded-xl bg-slate-50 p-3 text-xs text-slate-700">
              <strong>
                {ownedQueue.items.filter((item) => item.completed).length}
                {" / "}
                {ownedQueue.total}
              </strong>
              {" "}my judgments recorded for batch {ownedQueue.batch_id}.
              {ownedQueue.total > 0 && (
                <p className="mt-2 text-slate-500">
                  No other reviewer&apos;s labels, identities or passages are included.
                </p>
              )}
            </div>
          )}

          <div className="border-t border-slate-100 pt-4">
            <p className="text-xs font-semibold text-slate-700">
              Selected local fictional claim
            </p>
            <p dir="auto" className="mt-2 break-words text-sm font-medium text-slate-900">
              {localClaim?.claim_text ?? "First import the original C6 synthetic CSV below."}
            </p>
            {localClaim && (
              <p className="mt-1 break-all font-mono text-[10px] text-slate-500">
                {localClaim.review_key}
              </p>
            )}
            <p className="mt-3 text-xs text-slate-600">
              {matched
                ? "The backend source fingerprint matches your locally imported C6 claim."
                : "For a reviewer submission, first load YOUR assigned queue and a matching local C6 claim. A third adjudicator may submit only after a documented disagreement."}
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              disabled={busy || !batchIsValid || !matched || !input || !localClaim}
              className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-violet-700 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40"
              onClick={() => {
                if (!input || !localClaim) return;
                void execute(
                  () => submitOwnClaimReview(
                    selectedBatch, localClaim.review_key, input,
                  ),
                  () => {
                    setQueue(null); // stale completion is no longer actionable
                    setMessage("Your single immutable judgment was recorded by the authenticated backend.");
                  },
                );
              }}
            >
              <CheckCircle2 className="h-4 w-4" /> Submit my judgment
            </button>
            <button
              type="button"
              disabled={busy || !batchIsValid || !input || !localClaim}
              className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-2 text-xs font-semibold text-amber-800 disabled:opacity-40"
              onClick={() => {
                if (!input || !localClaim) return;
                void execute(
                  () => adjudicateSyntheticClaim(
                    selectedBatch, localClaim.review_key, input,
                  ),
                  () => setMessage(
                    "Third-person adjudication recorded. This does not approve a chatbot release.",
                  ),
                );
              }}
            >
              Adjudicate disputed claim
            </button>
          </div>
        </div>
      </div>

      {(error || message) && (
        <p
          role={error ? "alert" : "status"}
          className={
            "mt-4 rounded-xl border p-3 text-xs leading-6 " +
            (error
              ? "border-rose-200 bg-rose-50 text-rose-800"
              : "border-emerald-200 bg-emerald-50 text-emerald-800")
          }
        >
          {error || message}
        </p>
      )}
      <p className="mt-4 text-[11px] leading-5 text-slate-500">
        Only the pinned C6 fictional dataset. The C10/C11 service is not
        enabled outside isolated development CI. No real staff identities,
        customer transcripts, AI model execution, or production approval.
      </p>
    </section>
  );
}
