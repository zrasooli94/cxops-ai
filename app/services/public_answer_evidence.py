"""High-confidence *direct* source-polarity contradiction detection.

A citation proves only that a source ID was supplied. This conservative,
deterministic gate catches a narrow class of explicit English contradictions:
identical subject + copula + predicate, but opposite "not" polarity. A source
saying "Aster bicycle repairs are not free" directly conflicts with "All
Aster bicycle repairs are free [S1]".

This is NOT semantic entailment, translation, contradiction NLI, a public
source provenance proof or a model-based safety rating. Unmatched wording
stays unverified and may need human-labelled review. Use this only *after*
the existing tenant-scoped retrieval and citation-membership checks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.services.citation_service import CitationService

# Hard bounds on externally influenced model output and tenant source chunks.
MAX_PUBLIC_ANSWER_CHARS = 5_000
MAX_CITED_EVIDENCE_CHARS = 5_000

# Keep the detector deliberately narrow. These predicate words have a clear
# polarity when the *same* subject, verb and adjective appear verbatim.
_PREDICATE = (
    r"free|automatic|available|guaranteed|included|eligible|"
    r"refundable|allowed|permitted"
)
_ASSERTION = re.compile(
    r"\b(?P<subject>[A-Za-z][A-Za-z0-9' -]{2,90}?)\s+"
    r"(?P<verb>is|are|was|were)\s+"
    r"(?P<negation>not\s+)?"
    rf"(?P<predicate>{_PREDICATE})\b",
    re.IGNORECASE,
)
_ARTICLES = re.compile(r"^(?:(?:all|every|any|the|our|your)\s+)+", re.IGNORECASE)
_CITATION = re.compile(r"\[S[1-9][0-9]*\]")


def _normalized_subject(subject: str) -> str:
    collapsed = " ".join(subject.casefold().split())
    return _ARTICLES.sub("", collapsed).strip()


def _explicit_predicates(text: str) -> set[tuple[str, str, str, bool]]:
    """(subject, verb, predicate, negated) without parsing arbitrary language."""
    without_markers = _CITATION.sub("", text)
    return {
        (
            _normalized_subject(match.group("subject")),
            match.group("verb").casefold(),
            match.group("predicate").casefold(),
            match.group("negation") is not None,
        )
        for match in _ASSERTION.finditer(without_markers)
        if _normalized_subject(match.group("subject"))
    }


def contains_direct_source_contradiction(
    answer: str,
    sources: Sequence[Mapping[str, object]],
) -> bool:
    """Detect a directly opposing assertion in the *cited* source's content.

    No API, network, language model, persistence or user content logging.
    Requires a citation to the source in the answer; a decoy uncited source
    cannot change the outcome. Ambiguous forms intentionally return False
    and are NOT represented as supported.
    """
    if not answer or len(answer) > MAX_PUBLIC_ANSWER_CHARS:
        return False

    cited_ids = CitationService.extract(answer)
    if not cited_ids:
        return False
    claims = _explicit_predicates(answer)
    if not claims:
        return False

    for source in sources:
        if not isinstance(source, Mapping):
            continue
        source_id = source.get("source_id")
        source_text = source.get("content")
        if (
            not isinstance(source_id, str)
            or source_id not in cited_ids
            or not isinstance(source_text, str)
            or len(source_text) > MAX_CITED_EVIDENCE_CHARS
        ):
            continue

        evidence = _explicit_predicates(source_text)
        for subject, verb, predicate, negative in claims:
            if (subject, verb, predicate, not negative) in evidence:
                return True
    return False
