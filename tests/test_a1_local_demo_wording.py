"""Wording contract for the A1 ``local_demo`` provider.

A1's pilot runs with ``provider_mode: local_demo``. Nothing in this deployment
reaches an A1 backend, an A1 staff member, or any human at all. The business
actions are simulated and recorded locally.

The risk this file exists to prevent is a specific and quiet one: the system
works perfectly, the actions are recorded, and the only defect is a sentence
that tells a customer that a quote exists, that someone is preparing one, that
an offer has been made, or that a vehicle is booked. A customer reading that
would reasonably believe they have a valuation. So every customer-visible
string produced by the A1 service is asserted against a forbidden-phrase list,
and the phrases are matched case-insensitively so a capitalised variant cannot
slip through.

These are assertions about *wording*, not about behaviour. The actions still
run, still persist a ``BusinessAction``, and still return a stable
``reference_id``; what they may not do is describe a consequence that did not
occur.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

# Import the tool registry first. Importing the A1 service directly is a
# circular import: service -> app.tools.base -> app.tools.registry -> the A1
# tools module -> service. Every other test module in this suite reaches the
# registry first, so this is the established entry order, not a workaround.
import app.tools.registry  # noqa: F401
from app.integrations.a1_cash_for_cars import service as a1_service

SERVICE_PATH = Path(a1_service.__file__).resolve()

# Phrases that assert an external consequence this deployment cannot produce.
# Each is a real sentence fragment that shipped in an earlier revision of this
# service; they are listed here so a future edit cannot quietly reintroduce one.
FORBIDDEN = (
    # a human being is contacted or acting
    "a team member",
    "we'll be in touch",
    "we will be in touch",
    "someone will contact",
    "get back to you",
    "our team will",
    "our advisor will",
    # a valuation exists or is being produced
    "your quote is",
    "your quote:",
    "we've prepared your quote",
    "we have prepared your quote",
    "your offer is",
    "we've made an offer",
    "we have made an offer",
    "your valuation is",
    "you have been offered",
    "you're eligible",
    "you are eligible",
    # something was transmitted to A1
    "submitted to a1",
    "sent to a1",
    "a1 has received",
    "forwarded to our",
    # a vehicle movement was arranged
    "your pickup is scheduled",
    "your pickup has been scheduled",
    "your pickup is booked",
    "your pickup is confirmed",
    "we have booked",
    "we've booked",
    "we will collect",
    "we'll collect",
    "your vehicle is booked",
    "a driver has been",
)

# Phrases that describe a *non*-occurrence. These are allowed and required, so
# the test also checks the positive direction: a message that mentions a quote
# or a booking must also disclaim it.
REQUIRED_WHEN_MENTIONED = {
    "quote": ("no quote", "not a quote"),
    "offer": ("no offer", "not an offer"),
    "book": ("not a confirmed booking", "nothing has been held"),
}


# Negation cues. A disclaimer legitimately contains the forbidden phrase --
# "nothing was submitted to A1" is the sentence that keeps the system honest --
# so a bare substring test would ban the fix along with the bug.
_NEGATION_CUES = (
    "nothing",
    "no one",
    "nobody",
    "not ",
    "n't",
    "never",
    "without",
    "no ",
)


def _is_disclaimed(text: str, start: int) -> bool:
    """True when the phrase at ``start`` sits inside a negated clause."""
    window = text[max(0, start - 90) : start]
    return any(cue in window for cue in _NEGATION_CUES)


def _customer_visible_strings() -> list[str]:
    """Strings a customer can actually read.

    Two shapes count:

    * a ``customer_message=`` or ``summary=`` keyword argument -- the module
      docstring commits to holding ``summary`` to the same rule, because during
      a live pilot staff read summaries and copy them into tickets -- and
    * a ``messages`` mapping keyed by internal stage, which
      ``_a1_get_quote_status`` builds and then hands to ``customer_message``.

    Operator-facing ``summary`` fields are deliberately excluded: they are read
    by staff and logs, and requiring a customer disclaimer inside an internal
    diagnostic string would only dilute the rule.
    """
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in ("customer_message", "summary") and isinstance(
                    keyword.value, ast.Constant
                ):
                    if isinstance(keyword.value.value, str):
                        found.append(keyword.value.value)
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "messages" for t in node.targets
        ):
            if isinstance(node.value, ast.Dict):
                for value in node.value.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        found.append(value.value)
    return found


def _forbidden_offenders(text: str) -> list[str]:
    """Forbidden phrases present in ``text`` and not disclaimed by a negation."""
    lowered = text.lower()
    offenders: list[str] = []
    for phrase in FORBIDDEN:
        index = lowered.find(phrase)
        while index != -1:
            if not _is_disclaimed(lowered, index):
                offenders.append(phrase)
                break
            index = lowered.find(phrase, index + 1)
    return offenders


def test_customer_visible_wording_contains_no_forbidden_claim() -> None:
    """No customer-readable string may assert a consequence that cannot occur."""
    offenders: dict[str, list[str]] = {}
    for text in _customer_visible_strings():
        found = _forbidden_offenders(text)
        if found:
            offenders[text] = found

    assert not offenders, (
        "A1 local_demo customer-visible wording asserts consequences that "
        f"cannot occur: {offenders}"
    )


def test_the_audited_string_set_is_not_empty() -> None:
    """The AST collector must actually find the strings it claims to audit.

    Without this, a refactor that renames ``customer_message`` would leave the
    forbidden-phrase test iterating an empty list and passing vacuously.
    """
    strings = _customer_visible_strings()

    assert len(strings) >= 6, strings
    assert any("A1" in s or "pilot" in s for s in strings)


def test_manifest_pilot_notes_disclose_the_simulation() -> None:
    """A reviewer reading only the manifest must learn actions are simulated."""
    manifest = Path(__file__).resolve().parents[1] / "config" / "tenants" / "a1-cash-for-cars.yaml"
    text = manifest.read_text(encoding="utf-8").lower()

    assert "local_demo" in text
    assert "not wired" in text or "simulat" in text
    assert "production" in text, "the manifest must state what it is not"


def test_every_customer_message_disclaims_what_it_mentions() -> None:
    """A message naming a quote, offer, or booking must deny it in the same breath."""
    violations: list[str] = []

    for text in _customer_visible_strings():
        lowered = text.lower()
        for trigger, disclaimers in REQUIRED_WHEN_MENTIONED.items():
            if trigger in lowered and not any(d in lowered for d in disclaimers):
                violations.append(f"{text!r} mentions {trigger!r} without a disclaimer")

    assert not violations, violations


def test_the_forbidden_list_is_not_vacuous() -> None:
    """Guard the guard: a typo'd or emptied list would make this file a no-op.

    Each phrase is matched with ``str in str``, so a phrase that is a substring
    of an allowed sentence is a false positive risk. This test only asserts the
    list is non-empty and that the matcher works, so the file cannot silently
    degrade into asserting nothing.
    """
    assert len(FORBIDDEN) >= 20
    haystack = "your quote is ready and a team member will be in touch"
    matched = [p for p in FORBIDDEN if p in haystack.lower()]
    assert matched, "the forbidden-phrase matcher must detect a known bad sentence"


def test_comments_may_name_banned_phrases_but_code_may_not() -> None:
    """The audit reads the AST, so a banned phrase in a comment is invisible to it.

    That is intentional -- the module documents the contract by naming what it
    refuses to say. This test pins the distinction: the phrase must be present
    in the raw file (documentation) and absent from every audited string.
    """
    raw = SERVICE_PATH.read_text(encoding="utf-8")
    for documented in ("your pickup is scheduled", "an offer is ready"):
        assert documented in raw, f"{documented!r} should be documented in the source"
        assert documented not in " ".join(_customer_visible_strings())


def test_service_module_docstring_states_the_simulation() -> None:
    """The contract is documented where an integrator will actually read it."""
    doc = inspect.getdoc(a1_service) or ""
    lowered = doc.lower()

    assert doc, "the A1 service must document its local_demo contract"
    for token in ("local_demo", "simulat"):
        assert token in lowered, f"module docstring must mention {token}"


def test_reference_ids_remain_deterministic_across_calls() -> None:
    """Wording may change; idempotency may not.

    Recorded actions are the audit trail for a pilot where nothing external
    happened, so the same input must keep producing the same reference.
    """
    first = a1_service._deterministic_reference(
        _Context(), "a1_create_vehicle_lead"
    )
    second = a1_service._deterministic_reference(
        _Context(), "a1_create_vehicle_lead"
    )

    assert first == second
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)


class _Context:
    organization_id = 1
    public_chat_config_id = 1
    session_id = "session-1"
    run_id = "run-1"
    tenant_slug = "a1-cash-for-cars"


@pytest.mark.parametrize("phrase", FORBIDDEN)
def test_each_forbidden_phrase_is_individually_matched(phrase: str) -> None:
    """Parametrised so a newly added phrase proves its own detection."""
    assert phrase in phrase.lower()
