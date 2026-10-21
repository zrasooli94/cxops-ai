"""Zero-cost grounded chat provider for isolated local development.

This is deliberately not presented as an LLM. It exercises the real
tenant-scoped database, public session, conversation and handoff paths without
network calls. Replies copy one authored knowledge chunk verbatim, preventing
the provider from synthesizing an undocumented policy, price or promise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument

_TOKEN = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "about", "do", "does", "for", "how",
        "i", "in", "is", "it", "me", "of", "on", "that", "the", "to",
        "what", "when", "where", "you", "your",
        "آیا", "از", "است", "این", "با", "به", "برای", "چه", "در", "را",
        "شما", "که", "مورد", "می", "چیست",
    }
)
_ACTION_WORDS = frozenset(
    {
        "book", "buy", "cancel", "change", "contact", "order", "pay",
        "refund", "reserve", "schedule", "track",
        "پرداخت", "خرید", "رزرو", "سفارش", "لغو", "بازپرداخت",
    }
)
_FOLLOWUP_WORDS = frozenset({"it", "that", "those", "آن", "این", "همان"})


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(value.casefold())
        if len(token) > 1 and token not in _STOPWORDS
    }


def _raw_tokens(value: str) -> set[str]:
    return {token for token in _TOKEN.findall(value.casefold()) if len(token) > 1}


@dataclass(frozen=True)
class OfflineGroundedAnswer:
    text: str
    source_id: str
    title: str
    score: float


class OfflineGroundedChat:
    """Tenant-safe lexical retrieval with a strict authored-text response."""

    @staticmethod
    async def answer(
        db: AsyncSession,
        *,
        organization_id: int,
        question: str,
        previous_customer_question: str | None = None,
    ) -> OfflineGroundedAnswer | None:
        raw_question_tokens = _raw_tokens(question)
        if raw_question_tokens & _ACTION_WORDS:
            return None
        question_tokens = raw_question_tokens - _STOPWORDS

        if (
            previous_customer_question
            and raw_question_tokens & _FOLLOWUP_WORDS
            and not question_tokens
        ):
            question_tokens |= _tokens(previous_customer_question)
        if not question_tokens:
            return None

        rows = (
            await db.execute(
                select(KnowledgeChunk, KnowledgeDocument.title)
                .join(
                    KnowledgeDocument,
                    (KnowledgeDocument.id == KnowledgeChunk.document_id)
                    & (
                        KnowledgeDocument.organization_id
                        == KnowledgeChunk.organization_id
                    ),
                )
                .where(KnowledgeChunk.organization_id == organization_id)
                .order_by(KnowledgeChunk.id.asc())
                .limit(200)
            )
        ).all()

        best: tuple[float, KnowledgeChunk, str] | None = None
        for chunk, title in rows:
            searchable = _tokens(f"{title} {chunk.content}")
            overlap = question_tokens & searchable
            if not overlap:
                continue
            score = len(overlap) / max(1, len(question_tokens))
            candidate = (score, chunk, title)
            if best is None or candidate[0] > best[0]:
                best = candidate

        if best is None or best[0] < 0.34:
            return None
        score, chunk, title = best
        authored = chunk.content.strip()
        if not authored:
            return None
        return OfflineGroundedAnswer(
            text=f"{authored}\n\n[Source: {title}]",
            source_id=f"offline:{chunk.id}",
            title=title,
            score=score,
        )


offline_grounded_chat = OfflineGroundedChat()
