import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  deriveRagEvidenceState,
  ragEvidenceStateMessage,
  type RagEvidenceState,
} from "./evidence.ts";

function stateFor(
  workflowPath: string[],
  sources: unknown[],
): RagEvidenceState {
  return deriveRagEvidenceState({ workflowPath, sources });
}

describe("deriveRagEvidenceState", () => {
  it("sources present -> retrieval_performed_with_sources", () => {
    assert.equal(
      stateFor([], [{ document_id: 1 }]),
      "retrieval_performed_with_sources",
    );
    assert.equal(
      stateFor(["retrieve_knowledge"], [{ document_id: 1 }]),
      "retrieval_performed_with_sources",
    );
  });

  it("no sources + workflow contains retrieve_knowledge -> retrieval_performed_no_matches", () => {
    assert.equal(
      stateFor(["load_ticket", "retrieve_knowledge", "decide"], []),
      "retrieval_performed_no_matches",
    );
  });

  it("no sources + workflow contains rag -> retrieval_performed_no_matches", () => {
    assert.equal(
      stateFor(["rag"], []),
      "retrieval_performed_no_matches",
    );
  });

  it("no sources + workflow contains knowledge_gate -> retrieval_performed_no_matches", () => {
    assert.equal(
      stateFor(["knowledge_gate"], []),
      "retrieval_performed_no_matches",
    );
  });

  it("no sources + workflow does NOT contain retrieval step -> retrieval_not_required", () => {
    assert.equal(
      stateFor(["load_ticket", "decide"], []),
      "retrieval_not_required",
    );
  });

  it("empty workflow + no sources -> retrieval_not_required", () => {
    assert.equal(stateFor([], []), "retrieval_not_required");
  });

  it("handles missing/null inputs safely", () => {
    assert.equal(
      deriveRagEvidenceState({ workflowPath: undefined, sources: undefined }),
      "retrieval_not_required",
    );
    assert.equal(
      deriveRagEvidenceState({ workflowPath: null, sources: null }),
      "retrieval_not_required",
    );
  });

  it("matches case-insensitively", () => {
    assert.equal(
      stateFor(["RETRIEVE_KNOWLEDGE"], []),
      "retrieval_performed_no_matches",
    );
    assert.equal(
      stateFor(["RAG"], []),
      "retrieval_performed_no_matches",
    );
  });

  it("sources always win over workflow markers", () => {
    assert.equal(
      stateFor(["load_ticket"], [{ document_id: 1 }]),
      "retrieval_performed_with_sources",
    );
  });
});

describe("ragEvidenceStateMessage", () => {
  it("returns the canonical retrieval_not_required message", () => {
    assert.equal(
      ragEvidenceStateMessage("retrieval_not_required"),
      "Knowledge retrieval was not required for this workflow.",
    );
  });

  it("returns the canonical retrieval_performed_no_matches message", () => {
    assert.equal(
      ragEvidenceStateMessage("retrieval_performed_no_matches"),
      "Knowledge retrieval was performed, but no relevant policy source passed the retrieval threshold.",
    );
  });

  it("returns a message for retrieval_performed_with_sources", () => {
    assert.equal(
      ragEvidenceStateMessage("retrieval_performed_with_sources"),
      "Knowledge evidence was retrieved and used for this decision.",
    );
  });
});
