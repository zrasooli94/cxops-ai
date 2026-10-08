"""RAG reliability tests for citation repair and eligibility."""
from app.services.rag_reliability import public_answer_eligible


def test_public_answer_eligible_policy():
    result = {
        "grounded": True,
        "retrieval_count": 2,
        "answer": "test answer [S1]",
        "best_similarity": 0.8,
        "sources": [{"source_id": "S1"}],
    }
    assert public_answer_eligible(result)[0] is True
    assert public_answer_eligible(result)[1] == "eligible"


def test_public_answer_eligible_requires_grounding():
    result = {
        "grounded": False,
        "retrieval_count": 1,
        "answer": "test",
        "best_similarity": 0.8,
        "sources": [{"source_id": "S1"}],
    }
    assert public_answer_eligible(result)[0] is False
    assert public_answer_eligible(result)[1] == "not_grounded"


def test_public_answer_eligible_requires_sources():
    result = {
        "grounded": True,
        "retrieval_count": 0,
        "answer": "test",
        "best_similarity": 0.8,
        "sources": [],
    }
    assert public_answer_eligible(result)[0] is False
    assert public_answer_eligible(result)[1] == "no_sources"


def test_public_answer_eligible_requires_non_empty():
    result = {
        "grounded": True,
        "retrieval_count": 1,
        "answer": "",
        "best_similarity": 0.8,
        "sources": [{"source_id": "S1"}],
    }
    assert public_answer_eligible(result)[0] is False
    assert public_answer_eligible(result)[1] == "empty_answer"


def test_public_answer_eligible_no_similarity():
    result = {
        "grounded": True,
        "retrieval_count": 1,
        "answer": "test [S1]",
        "best_similarity": None,
        "sources": [{"source_id": "S1"}],
    }
    assert public_answer_eligible(result)[0] is False
    assert public_answer_eligible(result)[1] == "no_similarity"


def test_public_answer_eligible_fallback_answer():
    result = {
        "grounded": True,
        "retrieval_count": 1,
        "answer": "I don't have enough information in the knowledge base to answer that question.",
        "best_similarity": 0.8,
        "sources": [{"source_id": "S1"}],
    }
    assert public_answer_eligible(result)[0] is False
    assert public_answer_eligible(result)[1] == "fallback_answer"


def test_public_answer_eligible_citation_fallback():
    result = {
        "grounded": True,
        "retrieval_count": 1,
        "answer": "I found potentially relevant information, but I could not produce a sufficiently grounded answer with valid citations.",
        "best_similarity": 0.8,
        "sources": [{"source_id": "S1"}],
    }
    assert public_answer_eligible(result)[0] is False
    assert public_answer_eligible(result)[1] == "fallback_answer"


def test_public_answer_eligible_invalid_citations():
    result = {
        "grounded": True,
        "retrieval_count": 1,
        "answer": "test [S99]",
        "best_similarity": 0.8,
        "sources": [{"source_id": "S1"}],
    }
    assert public_answer_eligible(result)[0] is False
    assert public_answer_eligible(result)[1] == "invalid_citations"


def test_public_answer_eligible_success():
    result = {
        "grounded": True,
        "retrieval_count": 1,
        "answer": "test [S1]",
        "best_similarity": 0.8,
        "sources": [{"source_id": "S1"}],
    }
    assert public_answer_eligible(result)[0] is True
    assert public_answer_eligible(result)[1] == "eligible"


def test_public_answer_eligible_retrieval_count_but_empty_sources():
    result = {
        "grounded": True,
        "retrieval_count": 1,
        "answer": "test [S1]",
        "best_similarity": 0.8,
        "sources": [],
    }
    assert public_answer_eligible(result)[0] is False
    assert public_answer_eligible(result)[1] == "no_sources"


def test_public_answer_eligible_source_missing_id():
    result = {
        "grounded": True,
        "retrieval_count": 1,
        "answer": "test [S1]",
        "best_similarity": 0.8,
        "sources": [{"content": "x"}],
    }
    assert public_answer_eligible(result)[0] is False
    assert public_answer_eligible(result)[1] == "no_sources"
