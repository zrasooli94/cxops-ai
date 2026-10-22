"""Conservative, tenant-bound topic continuity for public knowledge replies.

A prior customer question helps resolve a short referential follow-up, e.g.
"What about weekends?", but it is NEVER a knowledge source or a reason to
execute tools. No history is sent on new topics, action requests, long turns,
a stale gap, or after a human takeover. The caller queries only the existing
public-message repository with trusted conversation and tenant IDs.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Protocol

from app.services.intent_classification import classify_intent

MAX_FOLLOWUP_CHARS = 120
MAX_PRIOR_QUESTION_CHARS = 240
MAX_CONTEXT_AGE = timedelta(minutes=30)
CONTEXT_MESSAGE_LIMIT = 3

_FOLLOWUP_EN = re.compile(
    r"^(?:(?:and|also)\s+)?(?:what|how)\s+about\s+"
    r"(?:it|that|this|those|them|its\s+[a-z-]+|"
    r"weekends?|weekdays?|saturdays?|sundays?)\??$"
    r"|^and\s+(?:on|for)\s+(?:weekends?|weekdays?|saturdays?|sundays?)\??$"
    r"|^(?:(?:and|also)\s+)?(?:weekends?|weekdays?|saturdays?|sundays?)\??$",
    re.IGNORECASE,
)
_FOLLOWUP_CORRECTION_OR_NEGATION = re.compile(
    r"\b(?:actually|instead|i\s+mean|i\s+meant|not|rather\s+than|sorry)\b"
    r"|(?:در\s+واقع|به\s+جای|منظورم|منظور\s+من|بلکه|نه(?:،|,|\s))",
    re.IGNORECASE,
)
_SIMPLE_DAY_FOLLOWUP_EN = re.compile(
    r"^(?:(?:and|also)\s+)?(?:on\s+|for\s+)?"
    r"(?:weekends?|weekdays?|saturdays?|sundays?)\s*\?$",
    re.IGNORECASE,
)
_FOLLOWUP_FA = re.compile(
    r"^(?:و\s+)?(?:آخر\s*هفته|روزهای?\s+تعطیل|شنبه|یکشنبه)"
    r"(?:\s+(?:چطور|چگونه|چی|چیه|چه\s+طور))?\s*[؟?]?$"
)
_UNTRUSTED_PRIOR = re.compile(
    r"\b(?:password|passcode|otp|secret|api\s+key|access\s+token"
    r"|credit\s+card|account\s+number|ignore\s+(?:all|your|previous)"
    r"|system\s+prompt|developer\s+mode|jailbreak)\b"
    r"|@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|رمز\s*عبور|شماره\s*حساب|دستورالعمل\s*سیستم",
    re.IGNORECASE,
)
_PERSIAN_QUESTION = re.compile(
    r"^(?:آیا|چه|چطور|چگونه|کجا|کی|چه\s+زمانی)\b"
)


class PublicMessage(Protocol):
    id: int
    conversation_id: int
    organization_id: int
    provider: str
    direction: str
    visibility: str
    body: str
    dedupe_key: str | None
    sent_at: datetime | None
    delivery_status: str | None


def is_referential_information_followup(question: str) -> bool:
    """Only a short, single-line, explicitly referential information request."""
    if (
        not question
        or len(question) > MAX_FOLLOWUP_CHARS
        or "\n" in question
        or "\r" in question
    ):
        return False
    normalized = question.strip()
    if _FOLLOWUP_CORRECTION_OR_NEGATION.search(normalized):
        return False
    if not (_FOLLOWUP_EN.fullmatch(normalized) or _FOLLOWUP_FA.fullmatch(normalized)):
        return False
    return (
        classify_intent(
            text=normalized,
            fast_path_action=None,
            has_policy_signal=False,
        )
        == "information"
        or _SIMPLE_DAY_FOLLOWUP_EN.fullmatch(normalized) is not None
        or _FOLLOWUP_FA.fullmatch(normalized) is not None
    )


def is_safe_previous_customer_question(question: str) -> bool:
    if (
        not question
        or len(question) > MAX_PRIOR_QUESTION_CHARS
        or "\n" in question
        or "\r" in question
        or _UNTRUSTED_PRIOR.search(question)
    ):
        return False
    return (
        classify_intent(
            text=question.strip(), fast_path_action=None, has_policy_signal=False
        ) == "information"
        or (_PERSIAN_QUESTION.match(question.strip()) is not None and "؟" in question)
    )


def previous_answered_customer_question(
    messages: list[PublicMessage],
    *,
    organization_id: int,
    conversation_id: int,
    current_message_id: int,
    current_question: str,
    blocked_answers: frozenset[str],
) -> str | None:
    """Resolve a *single preceding, already-answered* customer question.

    Require the exact sequence [customer question, CXOps public answer,
    current customer follow-up] in this organization and conversation. The
    answer must use the same client_message_id suffix as its inbound and must
    have been sent before the follow-up. Never use internal/staff/other-tenant
    messages or unacknowledged public replies.
    """
    if not is_referential_information_followup(current_question):
        return None
    if len(messages) != CONTEXT_MESSAGE_LIMIT:
        return None

    previous, answer, current = messages
    if (
        any(
            message.organization_id != organization_id
            or message.conversation_id != conversation_id
            or message.provider != "cxops"
            or message.visibility != "public"
            for message in messages
        )
        or previous.direction != "inbound"
        or answer.direction != "outbound"
        or current.direction != "inbound"
        or current.id != current_message_id
        or current.body != current_question
        or not isinstance(answer.body, str)
        or not answer.body.strip()
        or answer.body.strip() in blocked_answers
        or answer.delivery_status not in {None, "sent"}
    ):
        return None

    prior_key = previous.dedupe_key or ""
    if not prior_key.startswith("public_chat:"):
        return None
    if answer.dedupe_key != "public_chat_reply:" + prior_key.removeprefix(
        "public_chat:"
    ):
        return None

    first, second, third = previous.sent_at, answer.sent_at, current.sent_at
    if not all(
        isinstance(value, datetime) and value.tzinfo is not None
        for value in (first, second, third)
    ):
        return None
    if not (first <= second <= third and third - first <= MAX_CONTEXT_AGE):
        return None
    return previous.body.strip() if is_safe_previous_customer_question(previous.body) else None
