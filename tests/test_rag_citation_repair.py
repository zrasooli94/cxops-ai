"""Deterministic citation-repair tests for RAGService."""
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.rag_service import RAGService


@patch("app.services.rag_service.settings")
def test_invalid_first_answer_repair_succeeds(mock_settings):
    mock_settings.chat_model = "gpt-4o-mini"
    mock_settings.rag_top_k = 3
    service = RAGService()
    service.llm = AsyncMock()

    first_response = MagicMock()
    first_response.content = "Some answer text without citation"
    repaired_response = MagicMock()
    repaired_response.content = "Repaired answer [S1]"

    service.llm.ainvoke.side_effect = [first_response, repaired_response]

    async def run():
        with patch("app.services.knowledge_search_service.KnowledgeSearchService.search") as mock_search, \
             patch("app.services.ai_observability_service.AIObservabilityService.record", new_callable=AsyncMock):
            mock_search.return_value = [
                {
                    "content": "context",
                    "source_id": "S1",
                    "source_type": "document",
                    "similarity": 0.9,
                    "source_name": "doc1",
                    "chunk_id": "c1",
                    "document_id": "d1",
                }
            ]
            return await service.answer(
                db=None,
                organization_id=1,
                question="test",
            )

    import asyncio
    result = asyncio.run(run())
    assert service.llm.ainvoke.call_count == 2
    assert result["grounded"] is True
    assert "Repaired answer [S1]" in result["answer"]


@patch("app.services.rag_service.settings")
def test_invalid_first_answer_repair_fails_safe(mock_settings):
    mock_settings.chat_model = "gpt-4o-mini"
    mock_settings.rag_top_k = 3
    service = RAGService()
    service.llm = AsyncMock()

    first_response = MagicMock()
    first_response.content = "No citation"
    repaired_response = MagicMock()
    repaired_response.content = "Still no citation"

    service.llm.ainvoke.side_effect = [first_response, repaired_response]

    async def run():
        with patch("app.services.knowledge_search_service.KnowledgeSearchService.search") as mock_search, \
             patch("app.services.ai_observability_service.AIObservabilityService.record", new_callable=AsyncMock):
            mock_search.return_value = [
                {
                    "content": "context",
                    "source_id": "S1",
                    "source_type": "document",
                    "similarity": 0.9,
                    "source_name": "doc1",
                    "chunk_id": "c1",
                    "document_id": "d1",
                }
            ]
            return await service.answer(
                db=None,
                organization_id=1,
                question="test",
            )

    import asyncio
    result = asyncio.run(run())
    assert service.llm.ainvoke.call_count == 2
    assert result["grounded"] is False
    assert result["answer"] == (
        "I found potentially relevant information, but I could not "
        "produce a sufficiently grounded answer with valid citations."
    )


@patch("app.services.rag_service.settings")
def test_valid_first_answer_skips_repair(mock_settings):
    mock_settings.chat_model = "gpt-4o-mini"
    mock_settings.rag_top_k = 3
    service = RAGService()
    service.llm = AsyncMock()

    first_response = MagicMock()
    first_response.content = "Valid answer [S1]"

    service.llm.ainvoke.side_effect = [first_response]

    async def run():
        with patch("app.services.knowledge_search_service.KnowledgeSearchService.search") as mock_search, \
             patch("app.services.ai_observability_service.AIObservabilityService.record", new_callable=AsyncMock):
            mock_search.return_value = [
                {
                    "content": "context",
                    "source_id": "S1",
                    "source_type": "document",
                    "similarity": 0.9,
                    "source_name": "doc1",
                    "chunk_id": "c1",
                    "document_id": "d1",
                }
            ]
            return await service.answer(
                db=None,
                organization_id=1,
                question="test",
            )

    import asyncio
    result = asyncio.run(run())
    assert service.llm.ainvoke.call_count == 1
    assert result["grounded"] is True
    assert result["answer"] == "Valid answer [S1]"


@patch("app.services.rag_service.settings")
def test_negative_fact_repair_requires_valid_citation(mock_settings):
    mock_settings.chat_model = "gpt-4o-mini"
    mock_settings.rag_top_k = 3
    service = RAGService()
    service.llm = AsyncMock()

    first_response = MagicMock()
    first_response.content = "No info"
    repaired_response = MagicMock()
    repaired_response.content = "RISPU does not publish a phone number. [S1]"

    service.llm.ainvoke.side_effect = [first_response, repaired_response]

    async def run():
        with patch("app.services.knowledge_search_service.KnowledgeSearchService.search") as mock_search, \
             patch("app.services.ai_observability_service.AIObservabilityService.record", new_callable=AsyncMock):
            mock_search.return_value = [
                {
                    "content": "RISPU does not publish a phone number.",
                    "source_id": "S1",
                    "source_type": "document",
                    "similarity": 0.9,
                    "source_name": "doc1",
                    "chunk_id": "c1",
                    "document_id": "d1",
                }
            ]
            return await service.answer(
                db=None,
                organization_id=1,
                question="What is RISPU's phone number?",
            )

    import asyncio
    result = asyncio.run(run())
    assert service.llm.ainvoke.call_count == 2
    assert result["grounded"] is True
