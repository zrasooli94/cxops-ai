"""Public RAG answer eligibility: a defensive structural publication gate.

The source list and citations must agree, contain believable bounded retrieval
metadata, and have a non-fallback answer. This policy cannot prove that a
language-model claim is semantically supported by a source: synthetic fidelity
tests and qualified real-model review are separate, necessary gates.
"""

import math
import re

from app.core.config import settings
from app.services.citation_service import CitationService
from app.services.public_answer_evidence import (
    MAX_CITED_EVIDENCE_CHARS,
    MAX_PUBLIC_ANSWER_CHARS,
    contains_direct_source_contradiction,
)

FALLBACK_STRINGS = (
    "I don't have enough information in the knowledge base to answer that question.",
    (
        "I found potentially relevant information, but I could not "
        "produce a sufficiently grounded answer with valid citations."
    ),
)

_SOURCE_ID = re.compile(r"S[1-9][0-9]*\Z")


def public_answer_eligible(rag_result: dict) -> tuple[bool, str]:
    """Return (eligible, bounded_reason) for a *public* knowledge answer.

    This is defensive validation after RAGService's own retrieval filter and
    citation repair. In particular, a non-finite best-match score or an
    ambiguous/malformed set of source IDs must not become a public answer.
    A syntactically valid citation alone does NOT verify factual entailment.
    """
    if not isinstance(rag_result, dict) or not rag_result.get("grounded"):
        return False, "not_grounded"

    retrieval_count = rag_result.get("retrieval_count")
    if (
        isinstance(retrieval_count, bool)
        or not isinstance(retrieval_count, int)
        or retrieval_count < 1
    ):
        return False, "no_sources"

    sources = rag_result.get("sources")
    if not isinstance(sources, list) or not sources:
        return False, "no_sources"

    # Bare source_id objects from trusted legacy test fixtures remain valid.
    # A missing ID cannot be cited, and duplicate/malformed IDs make the
    # provenance ambiguous even when the model cites a plausible [S1].
    raw_ids = [
        source.get("source_id") if isinstance(source, dict) else None
        for source in sources
    ]
    if not any(isinstance(value, str) and value for value in raw_ids):
        return False, "no_sources"
    if (
        len(sources) > retrieval_count
        or any(
            not isinstance(source_id, str)
            or _SOURCE_ID.fullmatch(source_id) is None
            for source_id in raw_ids
        )
        or len(set(raw_ids)) != len(raw_ids)
    ):
        return False, "invalid_sources"

    answer = rag_result.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return False, "empty_answer"

    score = rag_result.get("best_similarity")
    if score is None:
        return False, "no_similarity"
    if (
        isinstance(score, bool)
        or not isinstance(score, (float, int))
        or not math.isfinite(score)
        or not 0 <= score <= 1
    ):
        return False, "invalid_similarity"
    if score < settings.rag_min_similarity:
        return False, "low_similarity"

    answer_text = answer.strip()
    if len(answer_text) > MAX_PUBLIC_ANSWER_CHARS:
        return False, "overlong_answer"

    if any(
        answer_text.casefold().startswith(fallback.casefold())
        for fallback in FALLBACK_STRINGS
    ):
        return False, "fallback_answer"

    citations_valid, _ = CitationService.validate(
        answer=answer_text, valid_source_ids=set(raw_ids)
    )
    if not citations_valid:
        return False, "invalid_citations"

    cited_source_ids = CitationService.extract(answer_text)
    if any(
        isinstance(source.get("content"), str)
        and len(source["content"]) > MAX_CITED_EVIDENCE_CHARS
        for source in sources
        if source["source_id"] in cited_source_ids
    ):
        # A source chunk too large for bounded inspection cannot be treated
        # as though the explicit contradiction check examined all of it.
        return False, "oversized_evidence"

    if contains_direct_source_contradiction(answer_text, sources):
        # The answer explicitly flips the polarity of the cited, same-subject
        # assertion. The existing handoff path handles this ineligible result.
        # This does NOT certify semantic support in all remaining answers.
        return False, "explicit_source_conflict"

    return True, "eligible"


__all__ = ["public_answer_eligible"]
