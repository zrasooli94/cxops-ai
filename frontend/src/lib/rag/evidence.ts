/**
 * Canonical RAG evidence states for agent analysis UIs.
 *
 * Derivation is based only on the persisted workflow path and the returned
 * source list. No raw reasoning or hidden LLM state is exposed.
 */
export type RagEvidenceState =
  | "retrieval_not_required"
  | "retrieval_performed_no_matches"
  | "retrieval_performed_with_sources";

export type DeriveRagEvidenceStateInput = {
  workflowPath: string[] | readonly string[] | undefined | null;
  sources: readonly unknown[] | undefined | null;
};

const KNOWLEDGE_RETRIEVAL_STEP_MARKERS = new Set([
  "retrieve_knowledge",
  "rag",
  "knowledge_gate",
]);

/**
 * Derive the canonical RAG evidence state from a workflow path and source list.
 *
 * - Sources present -> evidence was retrieved and passed threshold.
 * - No sources and workflow contains a retrieval step -> retrieval was
 *   attempted but no relevant source passed the threshold.
 * - No sources and workflow contains no retrieval step -> retrieval was not
 *   required for this workflow.
 */
export function deriveRagEvidenceState({
  workflowPath,
  sources,
}: DeriveRagEvidenceStateInput): RagEvidenceState {
  const sourceCount = sources?.length ?? 0;

  if (sourceCount > 0) {
    return "retrieval_performed_with_sources";
  }

  const path = workflowPath ?? [];
  const mightRetrieveKnowledge = path.some((step) =>
    KNOWLEDGE_RETRIEVAL_STEP_MARKERS.has(step.toLowerCase()),
  );

  if (!mightRetrieveKnowledge) {
    return "retrieval_not_required";
  }

  return "retrieval_performed_no_matches";
}

/**
 * Human-readable explanation for each canonical RAG evidence state.
 */
export function ragEvidenceStateMessage(
  state: RagEvidenceState,
): string {
  switch (state) {
    case "retrieval_not_required":
      return "Knowledge retrieval was not required for this workflow.";
    case "retrieval_performed_no_matches":
      return "Knowledge retrieval was performed, but no relevant policy source passed the retrieval threshold.";
    case "retrieval_performed_with_sources":
      return "Knowledge evidence was retrieved and used for this decision.";
  }
}
