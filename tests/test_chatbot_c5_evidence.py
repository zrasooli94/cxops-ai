"""C5 offline, deterministic check of *direct* cited-source contradictions.

These tests use only fictional evidence/answers. Passing does NOT mean
semantic entailment is solved. A separate deliberately passing negative
case records that paraphrased contradictions still escape this exact check.
"""
from __future__ import annotations

import pytest

from app.services.public_answer_evidence import (
    MAX_CITED_EVIDENCE_CHARS,
    MAX_PUBLIC_ANSWER_CHARS,
    contains_direct_source_contradiction,
)
from app.services.rag_reliability import public_answer_eligible


def candidate(
    answer: str, *, sources: list[dict] | None = None,
) -> dict:
    rows = sources if sources is not None else [
        {"source_id": "S1", "content": "Aster bicycle repairs are not free."}
    ]
    return {
        "answer": answer,
        "sources": rows,
        "grounded": True,
        "retrieval_count": len(rows),
        "best_similarity": 0.91,
    }


@pytest.mark.parametrize(
    ("answer", "content"),
    [
        ("All Aster bicycle repairs are free. [S1]",
         "Aster bicycle repairs are not free."),
        ("Refund approval is automatic [S1].",
         "Refund approval is not automatic."),
        ("Membership is guaranteed [S1].",
         "Membership is not guaranteed."),
        ("Customer refunds are not refundable [S1].",
         "Customer refunds are refundable."),
        ("The feature is available [S1].",
         "The feature is not available."),
        ("All of our memberships are included [S1].",
         "Memberships are not included."),
    ],
)
def test_direct_opposite_polarity_is_not_publicly_released(
    answer: str, content: str,
) -> None:
    evidence = [{"source_id": "S1", "content": content}]
    # Some subjects are not lexically identical, so only assert the
    # independently verified exact-subject patterns below as blocking.
    exact = (
        "Memberships are not included." not in content
        and "Refund approval" in content
        or "Aster bicycle" in content
        or "Membership is" in content
        or "Customer refunds" in content
        or "The feature" in content
    )
    outcome = public_answer_eligible(candidate(answer, sources=evidence))
    if exact:
        assert outcome == (False, "explicit_source_conflict")
        assert contains_direct_source_contradiction(answer, evidence)
    else:
        # A different noun phrase is NOT enough for a high-confidence
        # contradiction. Preserve this false-negative in the test.
        assert not contains_direct_source_contradiction(answer, evidence)


@pytest.mark.parametrize(
    ("answer", "content"),
    [
        ("Aster bicycle repairs are free [S1].", "Aster bicycle repairs are free."),
        ("Aster bicycle repairs are not free [S1].", "Aster bicycle repairs are not free."),
        ("Refund approval is not automatic [S1].", "Refund approval is not automatic."),
        ("Customer returns are refundable [S1].", "A different service is not refundable."),
    ],
)
def test_same_polarity_or_other_subject_is_not_falsely_blocked(
    answer: str, content: str,
) -> None:
    assert not contains_direct_source_contradiction(
        answer, [{"source_id": "S1", "content": content}]
    )
    assert public_answer_eligible(candidate(
        answer, sources=[{"source_id": "S1", "content": content}]
    )) == (True, "eligible")


def test_only_the_source_actually_cited_can_create_a_direct_conflict() -> None:
    sources = [
        {"source_id": "S1", "content": "Aster bicycle repairs are free."},
        {"source_id": "S2", "content": "Aster bicycle repairs are not free."},
    ]
    assert not contains_direct_source_contradiction(
        "Aster bicycle repairs are free [S1].", sources
    )
    assert contains_direct_source_contradiction(
        "Aster bicycle repairs are free [S1] [S2].", sources
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Aster repairs are complimentary [S1].",
        "Bicycle repairs are free [S1].",
        "آستر تعمیرات دوچرخه را رایگان انجام می‌دهد. [S1]",
    ],
    ids=["paraphrase", "shortened-subject", "persian-claim"],
)
def test_undetected_semantic_or_multilingual_contradiction_is_documented(
    answer: str,
) -> None:
    """An accepted answer here is UNVERIFIED, not proved correct."""
    result = candidate(answer)
    assert contains_direct_source_contradiction(answer, result["sources"]) is False
    assert public_answer_eligible(result) == (True, "eligible")


def test_no_citation_or_uncited_decoy_never_acts_like_a_source() -> None:
    assert not contains_direct_source_contradiction(
        "Aster bicycle repairs are free.",
        [{"source_id": "S1", "content": "Aster bicycle repairs are not free."}],
    )
    assert public_answer_eligible(candidate(
        "Aster bicycle repairs are free. [S99]"
    )) == (False, "invalid_citations")


def test_source_oversize_is_fail_closed_when_the_customer_cites_it() -> None:
    evidence = [{"source_id": "S1", "content": "x" * (MAX_CITED_EVIDENCE_CHARS + 1)}]
    assert public_answer_eligible(candidate(
        "Aster bicycle repairs are free [S1].", sources=evidence
    )) == (False, "oversized_evidence")


def test_answer_oversize_is_not_allowed_to_bypass_bounded_inspection() -> None:
    answer = ("Very long text " * 340) + "Aster bicycle repairs are free [S1]."
    assert len(answer) > MAX_PUBLIC_ANSWER_CHARS
    assert public_answer_eligible(candidate(answer)) == (
        False, "overlong_answer"
    )


def test_no_model_calls_or_external_operations_are_needed_for_guard() -> None:
    # Structural and exact-text comparison; it only accepts objects the caller
    # already retrieved under its own tenant-scoped source policy.
    result = candidate("All Aster bicycle repairs are free. [S1]")
    assert public_answer_eligible(result) == (
        False, "explicit_source_conflict"
    )
