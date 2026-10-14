"""Deterministic, tenant-neutral coordinator intent classification (Phase 1S).

Pure functions, no I/O. The labels (``information`` / ``action`` / ``mixed`` /
``none``) only decide *routing and eligibility*; every customer-facing answer
still has to pass retrieval, grounding, citation and eligibility checks, and
every action still needs human approval. The classifier's job is therefore to
be

* **permissive about question shape**: a visitor asking "can rispu develop
  mobile app ?" is asking for information whatever the company is called, and
* **strict about request shape**: a polite question that asks the business to
  *do* something for the visitor, to disclose someone else's data, or to
  reveal secrets is never informational.

Design: the earlier versions matched fixed phrases ("can you", "can i"), so a
third-person subject ("can rispu", "does your company", "is X one of your
services") had no signal. Interrogative form is now recognised structurally:
a clause that opens with an auxiliary/modal verb or a wh-word, whatever the
subject. Vetoes are checked first and are deliberately broader than the
question signals, because a veto can only move a message toward human review.
"""

from __future__ import annotations

import re
from typing import Literal

AgentIntent = Literal["information", "action", "mixed", "none"]

# --- action phrasing (unchanged semantics; substring on purpose) -------------
ACTION_INTENT_MARKERS = (
    "please",
    "i need",
    "i want",
    "i'd like",
    "would like",
    "help me",
    "fix",
    "stop",
    "reset",
    "cancel",
    "update",
    "change",
    "activate",
    "send me",
    "requesting",
    "investigate",
)

# "I want to know if ..." is an information request, not an action request.
_KNOW_PHRASE = re.compile(
    r"\b(?:i(?:'d| would| do| just)?\s+)?(?:want|like|need|wanna|have|got)\s+to\s+"
    r"(?:know|ask|find out|check|understand)\b|\bi\s+(?:just\s+)?wonder\b"
)

# --- question signals --------------------------------------------------------
# Words that make a question wherever they appear (word-bounded). These are the
# long-standing markers; they also count for action-phrased messages.
_CORE_QUESTION = re.compile(
    r"\b(?:what|how|when|which|why|does|can i|is it|are you|am i)\b"
    r"|\b(?:eligib|requirement)"
)
# Capability/identity phrasing. Not counted for an already action-phrased
# message, so "can you please call me back?" stays a pure ``action``.
_CAPABILITY_QUESTION = re.compile(
    r"\b(?:where|who|whom|whose|do you|did you|can you|could you|is there|"
    r"are there|tell me about)\b"
)
_WH_INITIAL = re.compile(r"^(?:what|how|when|where|which|why|who|whom|whose)\b")

_FILLER = re.compile(
    r"^(?:(?:hi|hello|hey|hiya|howdy|yo|good (?:morning|afternoon|evening)|ok|okay|"
    r"so|well|um|uh|quick question|question|just wondering|excuse me|sorry|thanks|"
    r"thank you|also|and|but|then)\b[\s,.:;!\-]*)+"
)

# Clause-initial auxiliary or modal => yes/no question about whatever subject
# follows ("can rispu ...", "does your company ...", "is mobile development ...").
# "do not", "do me a favour", "do the following" are imperatives, not questions.
_AUX_INITIAL = re.compile(
    r"^(?:can|could|does|did|is|are|will|would|do)\b(?!\s+(?:not|n't|me|it|this|that|the|a|an|my)\b)"
)

# Embedded questions: "wondering if rispu builds apps", "curious whether ...".
_EMBEDDED_QUESTION = re.compile(
    r"\b(?:wonder(?:ing)?|curious|unsure|not sure|asking|know)\b[^.?!\n]{0,40}"
    r"\b(?:if|whether)\b"
)

# --- vetoes ------------------------------------------------------------------
_INSTRUCTION_OVERRIDE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}"
    r"\b(?:instruction|rule|prompt|polic|guideline|system|previous|above|prior)"
    r"|\bsystem prompt\b|\byou are now\b|\bdeveloper mode\b|\bjailbreak"
    r"|\bpretend (?:to be|you)\b|\bact as\b|\breveal your (?:prompt|instructions)\b"
)

# Verbs that, in "<modal> <subject> <verb> ... <ownership/immediacy>", turn a
# capability question into a request.
_REQUEST_VERBS = (
    r"start|begin|launch|publish|release|deploy|increase|decrease|raise|lower|book|"
    r"schedule|hire|buy|purchase|order|sign|enrol|enroll|subscribe|unsubscribe|"
    r"build|develop|create|make|design|set up|setup|change|update|edit|modify|"
    r"replace|send|email|call|text|give|grant|delete|remove|cancel|close|terminate|"
    r"refund|charge|pay|transfer|reset|unlock|access|share|show|reveal|disclose|"
    r"export|download|view|see|upgrade|downgrade|pause|resume|activate|approve|"
    r"extend|provide|add|run|post|do|quote|fix|stop|improve|optimi[sz]e|migrate|"
    r"redesign|rebuild|take over|manage|handle|run"
)
_MODAL_REQUEST = re.compile(
    r"\b(?:can|could|will|would|may|shall|do|does|did)\s+"
    r"(?:you|i|we|they|rispu|[a-z0-9&.'\-]+|(?:your|the|this|that|our)\s+[a-z0-9&.'\-]+)\s+"
    r"(?:please\s+|now\s+|also\s+|just\s+|really\s+)?"
    rf"(?:{_REQUEST_VERBS})\b(?P<rest>[^?.!\n]*)"
)
_OWNERSHIP_OR_IMMEDIACY = re.compile(
    r"\b(?:my|our|mine|me|us|now|today|tonight|tomorrow|asap|immediately|"
    r"right away|this week|next week|guaranteed|for free|free of charge|discount)\b"
)

# Another party's data, or secrets: never informational, question or not.
_OTHER_PARTY = re.compile(
    r"\b(?:another|other|someone else'?s?|somebody else'?s?|different|all|every|any)\s+"
    r"(?:customer|client|company|companies|user|account|tenant|person|people|"
    r"organi[sz]ation|business)(?:s|'s|s')?\b"
    r"|\b(?:customer|client|user|company|tenant)s?'s?\s+(?:information|info|data|"
    r"details|records|documents|files|messages|conversations|account)\b"
)
_DATA_WORDS = re.compile(
    r"\b(?:access|see|view|show|share|reveal|list|export|give|tell|get|read|"
    r"download|look up|information|info|data|details|records|documents|files|"
    r"messages|conversations|password|email|phone|address|account|contact)\b"
)
_SENSITIVE = re.compile(
    r"\b(?:password|passcode|pin code|otp|one[- ]time (?:code|password)|"
    r"verification code|api key|secret key|access token|credentials?|ssn|"
    r"social security|credit card|card number|bank account|account number|"
    r"routing number)\b"
)


def _normalise(text: str) -> str:
    value = text.lower().replace("’", "'").replace("‘", "'")
    value = re.sub(r"\s+([?!.,;:])", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def _clauses(text: str) -> list[str]:
    """Sentence and comma-led clauses, each with leading filler removed."""
    pieces: list[str] = []
    for sentence in re.split(r"[.?!;\n]+", text):
        for part in re.split(r",\s+|\s+-\s+", sentence):
            cleaned = _FILLER.sub("", part.strip()).strip()
            if cleaned:
                pieces.append(cleaned)
    return pieces


def is_question(text: str, *, action_phrased: bool) -> bool:
    """Interrogative form, independent of what is being asked."""
    if _CORE_QUESTION.search(text):
        return True
    if action_phrased:
        return False
    if _CAPABILITY_QUESTION.search(text) or _EMBEDDED_QUESTION.search(text):
        return True
    return any(_AUX_INITIAL.match(clause) for clause in _clauses(text))


def is_commitment_request(text: str) -> bool:
    """A polite question that asks the business to *do* something for me."""
    for clause in _clauses(text):
        if _WH_INITIAL.match(clause):
            continue  # "how do I start my project?" is a how-to question
        for match in _MODAL_REQUEST.finditer(clause):
            if _OWNERSHIP_OR_IMMEDIACY.search(match.group("rest")):
                return True
    return False


def asks_for_other_party_data_or_secrets(text: str) -> bool:
    if _SENSITIVE.search(text):
        return True
    return bool(_OTHER_PARTY.search(text) and _DATA_WORDS.search(text))


def classify_intent(
    *,
    text: str,
    fast_path_action: str | None,
    has_policy_signal: bool,
) -> AgentIntent:
    """Label the request. ``text`` may be any case; it is normalised here."""
    if fast_path_action is not None:
        return "none"

    value = _normalise(text)

    if _INSTRUCTION_OVERRIDE.search(value):
        return "mixed"
    if asks_for_other_party_data_or_secrets(value):
        return "mixed"

    # "I want to know if ..." must not count as an action phrasing.
    action_text = _KNOW_PHRASE.sub(" ", value)
    action_phrased = any(marker in action_text for marker in ACTION_INTENT_MARKERS)
    question = is_question(value, action_phrased=action_phrased)

    if question and not action_phrased and is_commitment_request(value):
        return "mixed"

    if action_phrased:
        if not question and not has_policy_signal:
            return "action"
        return "mixed"

    if question:
        return "information"

    return "mixed"
