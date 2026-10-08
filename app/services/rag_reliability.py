"""RAG reliability helpers (read-only policy).

This module provides a pure eligibility helper for public-answer readiness. It
does not wire anything into public chat; wiring would be a separate change.
"""

from app.services.citation_service import CitationService

FALLBACK_STRINGS = (
    "I don't have enough information in the knowledge base to answer that question.",
)


def public_answer_eligible(rag_result: dict) -> tuple[bool, str]:
    """Return (eligible, reason) for public auto-reply consideration.

    At minimum require:
    - grounded == True
    - retrieval_count >= 1
    - answer is non-empty
    - best_similarity is not None
    - citations are valid against supplied source IDs
    - no grounding/citation fallback condition occurred
    """
    if rag_result is None:
        return False, "not_grounded"

    if not rag_result.get("grounded"):
        return False, "not_grounded"

    retrieval_count = rag_result.get("retrieval_count")
    if not retrieval_count or retrieval_count < 1:
        return False, "no_sources"

    answer = rag_result.get("answer") or ""
    if not answer.strip():
        return False, "empty_answer"

    if rag_result.get("best_similarity") is None:
        return False, "not_grounded"

    answer_text = answer.strip()
    if any(answer_text.startswith(s) or answer_text == s for s in FALLBACK_STRINGS):
        return False, "fallback_answer"

    sources = rag_result.get("sources") or []
    valid_source_ids = {s.get("source_id") for s in sources if s.get("source_id")}

    if valid_source_ids:
        citations_valid, _ = CitationService.validate(
            answer=answer_text,
            valid_source_ids=valid_source_ids,
        )
        if not citations_valid:
            return False, "invalid_citations"

    return True, "eligible"


__all__ = ["public_answer_eligible"]
