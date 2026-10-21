/**
 * Typed adapter for the UNMODIFIED C7 offline review codec.
 *
 * The C8 CI verifies byte-for-byte identity with the proven C7 copy at
 * tools/chatbot-claim-review/review-core.js. We do not recalculate or claim
 * cryptographic verification of the C6 source snapshots in the browser:
 * authoritative SHA-256 checks remain in Python C6 combine/score.
 */
import coreModule from "./review-core.js";

export type ReviewRole = 1 | 2;
export type ClaimLabel = "supported" | "contradicted" | "unsupported" | "unclear";
export interface ReviewRow {
  [key: string]: string;
  dataset_id: string;
  review_key: string;
  fingerprint: string;
  tenant_id: string;
  case_id: string;
  claim_id: string;
  claim_text: string;
  answer_text: string;
  cited_source_ids: string;
  cited_source_passages: string;
  reviewer_1: string;
  reviewer_1_label: string;
  reviewer_1_evidence: string;
  reviewer_1_note: string;
  reviewer_2: string;
  reviewer_2_label: string;
  reviewer_2_evidence: string;
  reviewer_2_note: string;
  adjudicator: string;
  adjudicated_label: string;
  adjudication_note: string;
}

interface ReviewCore {
  readonly EXPECTED_DATASET: string;
  readonly FIELDS: readonly string[];
  readonly LABELS: readonly ClaimLabel[];
  validateWorksheet(text: string, slot: ReviewRole, identity: string): ReviewRow[];
  parseCsv(text: string): string[][];
  serializeCsv(rows: string[][]): string;
  reviewStatus(
    row: ReviewRow,
    slot: ReviewRole,
    identity: string
  ): { complete: boolean; reason: string };
  progress(
    rows: ReviewRow[],
    slot: ReviewRole,
    identity: string
  ): { finished: number; total: number; complete: boolean };
  editRow(
    rows: ReviewRow[],
    index: number,
    slot: ReviewRole,
    identity: string,
    changes: Partial<{ label: string; evidence: string; note: string }>
  ): ReviewRow[];
  sourceBodies(passages: string): string[];
  exportReviewerCsv(
    rows: ReviewRow[],
    slot: ReviewRole,
    identity: string,
    options?: { allowIncomplete?: boolean }
  ): string;
}

// C7 UMD sets module.exports for bundled/Node use. No dynamic import, eval,
// network or browser storage; no additional provider SDK is loaded.
export const reviewCore = coreModule as unknown as ReviewCore;

export interface ScopedReview {
  /** Scope UI state to the trusted organization selected by the shell. */
  organizationId: number;
  reviewerRole: ReviewRole;
  reviewerId: string;
  rows: ReviewRow[];
}

export function reviewForOrganization(
  review: ScopedReview | null,
  organizationId: number
): ScopedReview | null {
  return review?.organizationId === organizationId ? review : null;
}

export const MAX_REVIEW_FILE_BYTES = 12_000_000;

export function validSyntheticReviewUpload(file: {
  size: number;
  name: string;
}): boolean {
  return (
    typeof file.name === "string" &&
    file.name.toLowerCase().endsWith(".csv") &&
    Number.isFinite(file.size) &&
    file.size > 0 &&
    file.size <= MAX_REVIEW_FILE_BYTES
  );
}
