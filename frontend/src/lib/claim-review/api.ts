export const REVIEW_API = "/api/backend/evaluations/claim-reviews";
export type ReviewLabel = "supported" | "contradicted" | "unsupported" | "unclear";
export interface Batch { id: number; dataset_id: string; item_count: number; created_at: string }
export interface QueueItem { review_key: string; fingerprint: string; completed: boolean }
export interface Queue { batch_id: number; total: number; items: QueueItem[] }
export interface Judgment { id: number; batch_id: number; review_key: string; event_kind: "review" | "adjudication"; label: ReviewLabel; created_at: string }
export interface Assignment { batch_id: number; assigned_slots: 2 }

export class ReviewApiError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(status === 401 ? "Authentication required." :
      status === 403 ? "Insufficient permissions." :
      status === 404 ? "Review preview disabled or record unavailable." :
      status === 409 ? "Review conflict." : "Reviewer request failed.");
    this.status = status;
  }
}
function safeId(value: number): string {
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error("Invalid batch ID");
  return String(value);
}
function safeKey(value: string): string {
  if (!/^[a-z][a-z0-9-]{0,79}:[a-z][a-z0-9-]{0,79}$/.test(value)) {
    throw new Error("Invalid claim key");
  }
  return value;
}
async function apiCall<T>(path: string, method: "GET" | "POST",
  body?: object, fetcher: typeof fetch = fetch): Promise<T> {
  const response = await fetcher(REVIEW_API + path, {
    method, cache: "no-store",
    headers: { "content-type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) throw new ReviewApiError(response.status);
  return await response.json() as T;
}
export const reviewApi = {
  createBatch: (f?: typeof fetch) => apiCall<Batch>("/synthetic-batches", "POST", undefined, f),
  assign: (batch: number, first: string, second: string, f?: typeof fetch) => {
    if (!first.trim() || !second.trim() || first.trim() === second.trim() ||
      first.length > 255 || second.length > 255) throw new Error("Distinct reviewers required");
    return apiCall<Assignment>("/batches/" + safeId(batch) + "/assignments", "POST", {
      reviewer_one_subject: first.trim(), reviewer_two_subject: second.trim(),
    }, f);
  },
  queue: (batch: number, f?: typeof fetch) =>
    apiCall<Queue>("/batches/" + safeId(batch) + "/my-queue", "GET", undefined, f),
  submit: (batch: number, key: string, label: ReviewLabel,
    evidence: string, rationale: string, f?: typeof fetch) =>
    apiCall<Judgment>("/batches/" + safeId(batch) + "/reviews/" + safeKey(key), "POST", {
      label, evidence_excerpt: evidence || null, rationale: rationale || null,
    }, f),
  adjudicate: (batch: number, key: string, label: ReviewLabel,
    evidence: string, rationale: string, f?: typeof fetch) =>
    apiCall<Judgment>("/batches/" + safeId(batch) + "/adjudications/" + safeKey(key), "POST", {
      label, evidence_excerpt: evidence || null, rationale: rationale || null,
    }, f),
};
