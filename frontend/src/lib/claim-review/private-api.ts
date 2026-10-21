/**
 * C12 synthetic-only staff reviewer API client.
 *
 * All calls use the EXISTING same-origin /api/backend proxy, which forwards
 * an existing authenticated session and selects the org from trusted cookie.
 * Never attach Authorization or X-CXOps-Organization-ID from browser input.
 * C11 enforces valid JWT -> membership -> RBAC -> reviewer assignment.
 *
 * This library is ONLY imported into a server-gated development-only UI.
 * It does not accept freeform API paths, tenant IDs or an actor subject.
 */
export const C12_REVIEWER_PATH = "/api/claim-review";
export type ClaimLabel = "supported" | "contradicted" | "unsupported" | "unclear";

export interface SyntheticBatch {
  id: number;
  dataset_id: "c6-fictional-faq-20261021";
  item_count: 12;
  created_at: string;
}
export interface AssignmentResult {
  batch_id: number;
  assigned_slots: 2;
}
export interface OwnClaim {
  review_key: string;
  fingerprint: string;
  completed: boolean;
}
export interface OwnClaimQueue {
  batch_id: number;
  items: OwnClaim[];
  total: number;
}
export interface RecordedJudgment {
  id: number;
  batch_id: number;
  review_key: string;
  event_kind: "review" | "adjudication";
  label: ClaimLabel;
  created_at: string;
}

const REVIEW_KEY = /^[a-z][a-z0-9-]{0,79}:[a-z][a-z0-9-]{0,79}$/;
const FINGERPRINT = /^[a-f0-9]{64}$/;
const LABELS: readonly ClaimLabel[] = [
  "supported", "contradicted", "unsupported", "unclear",
];

export class C12ReviewApiError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(
      status === 401 || status === 403
        ? "Review access has changed or expired. Reload the staff page."
        : status === 404
          ? "The synthetic reviewer preview is unavailable or this batch cannot be accessed."
          : status === 409
            ? "This review has already been recorded or the batch state has changed."
            : status === 422 || status === 400
              ? "Synthetic reviewer input was rejected."
              : "Could not complete the synthetic reviewer request.",
    );
    this.name = "C12ReviewApiError";
    this.status = status;
  }
}
function validBatch(batchId: number): number {
  if (!Number.isSafeInteger(batchId) || batchId < 1 || batchId > 2147483647) {
    throw new Error("Select a valid positive review batch ID.");
  }
  return batchId;
}
function validKey(key: string): string {
  if (typeof key !== "string" || !REVIEW_KEY.test(key)) {
    throw new Error("Choose a valid fictional claim from the local C6 worksheet.");
  }
  return key;
}
function validSubject(subject: string): string {
  if (
    typeof subject !== "string" ||
    subject.trim() !== subject ||
    subject.length < 1 ||
    subject.length > 255 ||
    /[\r\n]/.test(subject)
  ) {
    throw new Error("Reviewer subject ID must be 1–255 characters.");
  }
  return subject;
}
export function validReviewLabel(value: string): value is ClaimLabel {
  return LABELS.includes(value as ClaimLabel);
}
export function ownQueueMatchesLocalClaim(
  queue: OwnClaimQueue | null,
  reviewKey: string,
  localFingerprint: string,
): boolean {
  if (
    !queue ||
    !REVIEW_KEY.test(reviewKey) ||
    !FINGERPRINT.test(localFingerprint)
  ) return false;
  const match = queue.items.find((row) => row.review_key === reviewKey);
  return (
    !!match &&
    FINGERPRINT.test(match.fingerprint) &&
    match.fingerprint === localFingerprint &&
    match.completed === false
  );
}
async function call<T>(
  path: string,
  method: "GET" | "POST",
  payload: object | undefined,
  fetcher: typeof fetch,
): Promise<T> {
  const response = await fetcher(C12_REVIEWER_PATH + path, {
    method,
    cache: "no-store",
    credentials: "same-origin",
    headers: {
      accept: "application/json",
      ...(payload === undefined ? {} : { "content-type": "application/json" }),
    },
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
  });
  if (!response.ok) {
    // Never render a raw backend error: could contain SQL/tenant/reviewer info.
    throw new C12ReviewApiError(response.status);
  }
  return (await response.json()) as T;
}
export async function createSyntheticBatch(
  fetcher: typeof fetch = fetch,
): Promise<SyntheticBatch> {
  // No body; C11 rejects arbitrary JSON, tenant IDs or subject IDs.
  return call("/synthetic-batches", "POST", undefined, fetcher);
}
export async function assignSyntheticReviewers(
  batchId: number,
  firstSubject: string,
  secondSubject: string,
  fetcher: typeof fetch = fetch,
): Promise<AssignmentResult> {
  validBatch(batchId);
  const a = validSubject(firstSubject), b = validSubject(secondSubject);
  if (a === b) throw new Error("Reviewer assignments must use different staff IDs.");
  return call(
    `/batches/${batchId}/assignments`,
    "POST",
    { reviewer_one_subject: a, reviewer_two_subject: b },
    fetcher,
  );
}
export async function fetchOwnClaimQueue(
  batchId: number,
  fetcher: typeof fetch = fetch,
): Promise<OwnClaimQueue> {
  validBatch(batchId);
  const result = await call<OwnClaimQueue>(
    `/batches/${batchId}/my-queue`, "GET", undefined, fetcher,
  );
  // The response is strictly metadata and must never contain raw source.
  if (
    result.batch_id !== batchId ||
    !Array.isArray(result.items) ||
    result.items.length > 500 ||
    result.total !== result.items.length ||
    result.items.some((item) =>
      !REVIEW_KEY.test(item.review_key) ||
      !FINGERPRINT.test(item.fingerprint) ||
      typeof item.completed !== "boolean"
    )
  ) throw new Error("Review queue response failed the synthetic contract.");
  return result;
}
export interface ReviewInput {
  label: ClaimLabel;
  evidence_excerpt?: string;
  rationale?: string;
}
function verifiedReviewInput(input: ReviewInput): ReviewInput {
  if (!validReviewLabel(input.label)) throw new Error("Unknown review label.");
  if (
    input.evidence_excerpt !== undefined &&
    input.evidence_excerpt.length > 1000
  ) throw new Error("Evidence excerpt exceeds the 1,000-character limit.");
  if (input.rationale !== undefined && input.rationale.length > 500) {
    throw new Error("Rationale exceeds the 500-character limit.");
  }
  return {
    label: input.label,
    ...(input.label === "supported" || input.label === "contradicted"
      ? { evidence_excerpt: input.evidence_excerpt ?? "" }
      : { rationale: input.rationale ?? "" }),
  };
}
export async function submitOwnClaimReview(
  batchId: number,
  reviewKey: string,
  input: ReviewInput,
  fetcher: typeof fetch = fetch,
): Promise<RecordedJudgment> {
  validBatch(batchId);
  return call(
    `/batches/${batchId}/reviews/${validKey(reviewKey)}`,
    "POST", verifiedReviewInput(input), fetcher,
  );
}
export async function adjudicateSyntheticClaim(
  batchId: number,
  reviewKey: string,
  input: ReviewInput,
  fetcher: typeof fetch = fetch,
): Promise<RecordedJudgment> {
  validBatch(batchId);
  return call(
    `/batches/${batchId}/adjudications/${validKey(reviewKey)}`,
    "POST", verifiedReviewInput(input), fetcher,
  );
}
