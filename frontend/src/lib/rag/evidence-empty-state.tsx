import {
  deriveRagEvidenceState,
  ragEvidenceStateMessage,
} from "./evidence.ts";

export type RagEvidenceEmptyStateProps = {
  workflowPath: string[] | readonly string[] | undefined | null;
  sources: readonly unknown[] | undefined | null;
};

/**
 * Compact, canonical empty-state panel for RAG evidence.
 *
 * Use this when there are no sources so the three canonical states are
 * rendered consistently across every analysis UI.
 */
export function RagEvidenceEmptyState({
  workflowPath,
  sources,
}: RagEvidenceEmptyStateProps) {
  const state = deriveRagEvidenceState({ workflowPath, sources });
  const isNoMatches = state === "retrieval_performed_no_matches";

  return (
    <div
      className={`rounded-2xl border p-5 text-sm ${
        isNoMatches
          ? "border-amber-200 bg-amber-50/70 text-amber-700"
          : "border-slate-200 bg-slate-50/70 text-slate-500"
      }`}
    >
      {ragEvidenceStateMessage(state)}
    </div>
  );
}
