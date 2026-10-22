"""Zero-cost grounded chat provider for isolated local development.

This is deliberately not presented as an LLM. It exercises the real
tenant-scoped database, public session, conversation and handoff paths without
network calls. Replies copy one authored knowledge chunk verbatim, preventing
the provider from synthesizing an undocumented policy, price or promise.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.services.chat_followup_context import (
    is_referential_information_followup,
    is_safe_previous_customer_question,
)

_TOKEN = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "about", "do", "does", "for", "how",
        "i", "in", "is", "it", "its", "me", "of", "on", "that", "the", "to",
        "what", "when", "where", "you", "your",
        "آیا", "از", "است", "این", "با", "به", "برای", "چه", "در", "را",
        "شما", "که", "مورد", "می", "چیست",
    }
)
_ACTION_REQUEST = re.compile(
    r"\b(book|buy|cancel|change my|contact|order|pay|refund me|reserve|schedule|track)\b"
    r"|پرداخت|خرید|رزرو|ثبت سفارش|لغو|بازپرداخت (کنید|می خواهم|می‌خواهم)",
    re.IGNORECASE,
)
_AMBIGUOUS_NEGATION = re.compile(r"\bnot\b|(?:^|[\s،,])نه(?:[\s،,.!?؟]|$)", re.IGNORECASE)
_FOLLOWUP_WORDS = frozenset({"it", "that", "those", "آن", "این", "همان"})
_DOCUMENT_INSTRUCTION = re.compile(
    r"\b(?:ignore|disregard|forget)\b.{0,40}\b(?:instruction|prompt|rule)\b"
    r"|\b(?:system|developer)\s+(?:message|prompt)\b"
    r"|دستور(?:العمل)?\s*(?:سیستم|قبلی)|پیام\s*سیستم",
    re.IGNORECASE,
)
_PERSIAN_VARIANTS = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})
_NUMBER = re.compile(r"(?<!\w)\d+(?:[.,٫٬]\d+)*(?!\w)")
_QUANTITY_PART = re.compile(r"\d+(?:[.,٫٬]\d+)*|[\w\u0600-\u06ff]+|[%$€£]", re.UNICODE)
_QUANTITY_CONTEXT = {
    "%": "percent", "percent": "percent", "percentage": "percent", "درصد": "percent",
    "rm": "currency", "رینگیت": "currency", "تومان": "currency", "ریال": "currency",
    "$": "currency", "€": "currency", "£": "currency",
    "month": "month", "months": "month", "ماه": "month",
    "day": "day", "days": "day", "روز": "day",
    "year": "year", "years": "year", "سال": "year",
    "hour": "hour", "hours": "hour", "ساعت": "hour",
    "kg": "weight", "kilogram": "weight", "kilograms": "weight", "کیلوگرم": "weight",
}
_NUMBER_WORDS = frozenset({
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "ده",
})
_NUMBER_WORD_VALUES = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "یک": "1", "دو": "2", "سه": "3", "چهار": "4",
    "پنج": "5", "شش": "6", "هفت": "7", "هشت": "8", "ده": "10",
}
_PAGE_SIZE = 200
_MAX_CHUNKS = 5000


def _normalize(value: str) -> str:
    """Normalize user/authored text for bilingual lexical comparison only."""
    value = unicodedata.normalize("NFKC", value).translate(_PERSIAN_VARIANTS)
    value = value.replace("\u200c", " ").replace("\u200f", " ").replace("\u200e", " ")
    return "".join(
        " " if unicodedata.category(char).startswith(("P", "S")) else char
        for char in value
        if unicodedata.category(char) not in {"Mn", "Cf"}
    ).casefold()


def _canonical_number(value: str) -> str | None:
    value = value.translate(_PERSIAN_VARIANTS).replace("٬", ",").replace("٫", ".")
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", value):
        return None
    if "," in value and "." in value:
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", value):
            return None
        value = value.replace(",", "")
    elif "," in value:
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", value):
            value = value.replace(",", "")
        elif value.count(",") == 1:
            value = value.replace(",", ".")
        else:
            return None
    elif value.count(".") > 1:
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
            value = value.replace(".", "")
        else:
            return None
    if "." in value:
        whole, fraction = value.split(".", 1)
        return f"{int(whole)}.{fraction.rstrip('0') or '0'}"
    return str(int(value))


def _quantity_facts(value: str) -> set[tuple[str, str]]:
    """Extract conservative numeric value/unit pairs for contradiction checks only."""
    normalized = unicodedata.normalize("NFKC", value).translate(_PERSIAN_VARIANTS).casefold()
    parts = _QUANTITY_PART.findall(normalized)
    facts: set[tuple[str, str]] = set()
    for index, part in enumerate(parts):
        if not _NUMBER.fullmatch(part) and part not in _NUMBER_WORDS:
            continue
        nearby = parts[max(0, index - 2):index] + parts[index + 1:index + 3]
        contexts = {_QUANTITY_CONTEXT[token] for token in nearby if token in _QUANTITY_CONTEXT}
        if len(contexts) == 1:
            number = _NUMBER_WORD_VALUES[part] if part in _NUMBER_WORD_VALUES else _canonical_number(part)
            if number is not None:
                facts.add((number, contexts.pop()))
    return facts


def _has_numeric_conflict(left: str, right: str) -> bool:
    left_facts = _quantity_facts(left)
    right_facts = _quantity_facts(right)
    shared_contexts = {context for _, context in left_facts} & {context for _, context in right_facts}
    for context in shared_contexts:
        left_values = {number for number, fact_context in left_facts if fact_context == context}
        right_values = {number for number, fact_context in right_facts if fact_context == context}
        # Equal sets and strict subsets are compatible (one source may add a
        # second fact). Disjoint or partially overlapping sets are ambiguous;
        # lexical extraction cannot bind each value to its predicate safely.
        if left_values != right_values and not (
            left_values <= right_values or right_values <= left_values
        ):
            return True
    return False


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(_normalize(value))
        if len(token) > 1 and token not in _STOPWORDS
    }


def _raw_tokens(value: str) -> set[str]:
    return {token for token in _TOKEN.findall(_normalize(value)) if len(token) > 1}


@dataclass(frozen=True)
class OfflineGroundedAnswer:
    text: str
    source_id: str
    title: str
    score: float


class OfflineGroundedChat:
    """Tenant-safe lexical retrieval with a strict authored-text response."""

    @staticmethod
    async def source_is_current(
        db: AsyncSession,
        *,
        organization_id: int,
        source_id: str,
    ) -> bool:
        """Check that a citation still belongs to current tenant knowledge.

        Retrieval and reply persistence are separate statements. An owner can
        replace or remove knowledge between them, so public chat checks this
        immediately before publishing. Historical messages and logs are never
        rewritten by this current-knowledge check.
        """
        if not source_id.startswith("offline:"):
            return False
        try:
            chunk_id = int(source_id.removeprefix("offline:"))
        except ValueError:
            return False
        current = await db.scalar(
            select(KnowledgeChunk.id)
            .join(
                KnowledgeDocument,
                (KnowledgeDocument.id == KnowledgeChunk.document_id)
                & (KnowledgeDocument.organization_id == KnowledgeChunk.organization_id),
            )
            .where(
                KnowledgeChunk.id == chunk_id,
                KnowledgeChunk.organization_id == organization_id,
                KnowledgeDocument.organization_id == organization_id,
            )
            # Hold a shared row lock through reply persistence. A concurrent
            # delete/replacement that starts after this check waits, giving a
            # clear commit order without locking during retrieval/generation.
            .with_for_update(read=True)
        )
        return current is not None

    @staticmethod
    async def answer(
        db: AsyncSession,
        *,
        organization_id: int,
        question: str,
        previous_customer_question: str | None = None,
    ) -> OfflineGroundedAnswer | None:
        raw_question_tokens = _raw_tokens(question)
        if _ACTION_REQUEST.search(question):
            return None
        # Lexical ranking cannot bind a negated product/entity reliably. When a
        # prior topic exists, clarify instead of allowing both old and negated
        # entity tokens to compete for an authored answer.
        if _AMBIGUOUS_NEGATION.search(question):
            return None
        question_tokens = raw_question_tokens - _STOPWORDS
        current_topic_tokens = set(question_tokens)
        pronoun_only = False

        if previous_customer_question and is_safe_previous_customer_question(
            previous_customer_question
        ):
            referential = is_referential_information_followup(question)
            pronoun_only = bool(raw_question_tokens & _FOLLOWUP_WORDS) and not question_tokens
            if referential or pronoun_only:
                question_tokens |= _tokens(previous_customer_question)
        if not question_tokens:
            return None

        candidates: list[tuple[float, int, KnowledgeChunk, str]] = []
        last_chunk_id = 0
        chunks_seen = 0
        while chunks_seen < _MAX_CHUNKS:
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
                    .where(
                        KnowledgeChunk.organization_id == organization_id,
                        KnowledgeChunk.id > last_chunk_id,
                    )
                .order_by(KnowledgeChunk.id.asc())
                    .limit(_PAGE_SIZE)
                )
            ).all()
            if not rows:
                break
            chunks_seen += len(rows)
            last_chunk_id = rows[-1][0].id
            for chunk, title in rows:
                if not chunk.content.strip() or _DOCUMENT_INSTRUCTION.search(chunk.content):
                    continue
                title_tokens = _tokens(title)
                content_tokens = _tokens(chunk.content)
                searchable = title_tokens | content_tokens
                overlap = question_tokens & searchable
                if not overlap:
                    continue
                coverage = len(overlap) / max(1, len(question_tokens))
                title_hits = len(question_tokens & title_tokens)
                precision = len(overlap) / max(1, min(len(searchable), 12))
                score = coverage + min(title_hits, 2) * 0.15 + precision * 0.1
                candidates.append((score, len(overlap), chunk, title))
            if len(rows) < _PAGE_SIZE:
                break

        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2].id))
        score, overlap_count, chunk, title = candidates[0]
        if score < 0.36 or (overlap_count < 2 and score < 0.55):
            return None
        if current_topic_tokens and not pronoun_only:
            selected_tokens = _tokens(f"{title} {chunk.content}")
            if not (current_topic_tokens & selected_tokens):
                return None

        # Only a concrete contradictory quantity is treated as a conflict. Merely
        # different or complementary near-tied prose is not enough to force handoff.
        if len(candidates) > 1:
            runner_score, runner_overlap, runner, _ = candidates[1]
            shared_context = _tokens(chunk.content) & _tokens(runner.content)
            if (
                runner_overlap == overlap_count
                and score - runner_score < 0.08
                and len(shared_context) >= 3
                and _has_numeric_conflict(chunk.content, runner.content)
            ):
                return None
        authored = chunk.content.strip()
        return OfflineGroundedAnswer(
            text=f"{authored}\n\n[Source: {title}]",
            source_id=f"offline:{chunk.id}",
            title=title,
            score=score,
        )


offline_grounded_chat = OfflineGroundedChat()
