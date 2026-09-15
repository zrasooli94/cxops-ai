from typing import Any

from app.core.config import settings


def filter_and_format_sources(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Apply adaptive similarity threshold and format sources for RAG consumption.

    This is the single source of truth for:
    - Adaptive threshold: max(min_similarity, best_similarity - margin)
    - Max sources limit
    - Source ID assignment (S1, S2, ...)
    - Metadata/title extraction with fallback
    """
    if not matches:
        return []

    best_similarity = float(matches[0]["similarity"])

    threshold = max(
        settings.rag_min_similarity,
        best_similarity - settings.rag_similarity_margin,
    )

    relevant_matches = [
        match for match in matches if float(match["similarity"]) >= threshold
    ][: settings.rag_max_sources]

    sources: list[dict[str, Any]] = []

    for index, match in enumerate(relevant_matches, start=1):
        metadata = match.get("metadata") or {}

        sources.append(
            {
                "source_id": f"S{index}",
                "chunk_id": match["chunk_id"],
                "document_id": match["document_id"],
                "title": metadata.get("title", "Unknown document"),
                "content": match["content"],
                "similarity": float(match["similarity"]),
            }
        )

    return sources
