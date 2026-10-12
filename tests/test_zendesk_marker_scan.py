"""Phase 1P.8R — complete, bounded, fail-closed Zendesk marker verification.

Unit tests drive ``find_marker`` with a pure fake page source; flow tests run
the real reply delivery path with the fault-injection ``FakeZendesk`` (real
Zendesk paging shape) against PostgreSQL. Any real network call fails.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.integrations.zendesk.client import ZendeskAPIError, ZendeskClient
from app.integrations.zendesk.marker_scan import (
    MARKER_SCAN_MAX_PAGES,
    MarkerScanInconclusive,
    find_marker,
    marker_boundary,
)
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from scripts import worker
from tests import test_job_fault_injection as harness

_no_network = harness._no_network
org_scope = harness.org_scope
zendesk = harness.zendesk

MARKER = "CXOps Reply Ref: token-123"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
BOUNDARY = NOW - timedelta(minutes=10)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class PageSource:
    """Newest-first cursor pages over a fixed list (index 0 = newest)."""

    def __init__(self, comments: list[dict], size: int = 100) -> None:
        self.comments = comments
        self.size = size
        self.calls: list[str | None] = []
        self.mutate = None  # optional hook(page_no, response) -> response

    async def __call__(self, cursor: str | None) -> dict:
        self.calls.append(cursor)
        start = 0 if cursor is None else int(cursor)
        page = self.comments[start : start + self.size]
        has_more = start + self.size < len(self.comments)
        response: dict = {"comments": page, "meta": {"has_more": has_more}}
        if has_more:
            response["meta"]["after_cursor"] = str(start + self.size)
        if self.mutate is not None:
            response = self.mutate(len(self.calls), response)
        return response


def _thread(marker_position: int | None, *, newer_total: int, older: int = 0) -> list[dict]:
    """``newer_total`` comments created after the boundary (newest first),
    the marker at ``marker_position`` (1-based) among them, then ``older``
    comments from before the boundary."""
    comments = []
    for index in range(newer_total):
        created = NOW + timedelta(minutes=newer_total - index)
        body = MARKER if marker_position == index + 1 else "newer"
        comments.append({"id": index + 1, "body": body, "created_at": _stamp(created)})
    for index in range(older):
        created = BOUNDARY - timedelta(minutes=index + 1)
        comments.append(
            {"id": newer_total + index + 1, "body": "older", "created_at": _stamp(created)}
        )
    return comments


# ---------------------------------------------------------------------------
# find_marker: positions and conclusive outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("position", [1, 50, 51, 100, 101, 250, 1999])
@pytest.mark.asyncio
async def test_marker_is_found_at_any_position_within_budget(position) -> None:
    source = PageSource(_thread(position, newer_total=max(position, 2000)))

    result = await find_marker(source, marker=MARKER, not_before=BOUNDARY)

    assert result.found and result.comment_id == str(position)
    assert result.pages == math.ceil(position / 100)
    assert result.conclusion == "found"


@pytest.mark.asyncio
async def test_absence_is_proven_by_reaching_the_boundary() -> None:
    source = PageSource(_thread(None, newer_total=150, older=5000))

    result = await find_marker(source, marker=MARKER, not_before=BOUNDARY)

    assert not result.found
    assert result.conclusion == "reached_boundary"
    assert result.pages == 2, "old comments end the scan; their volume costs nothing"


@pytest.mark.asyncio
async def test_absence_is_proven_when_zendesk_has_no_more_pages() -> None:
    source = PageSource(_thread(None, newer_total=130))

    result = await find_marker(source, marker=MARKER, not_before=BOUNDARY)

    assert (result.found, result.conclusion, result.pages) == (False, "exhausted", 2)


@pytest.mark.asyncio
async def test_empty_ticket_is_conclusively_empty() -> None:
    result = await find_marker(PageSource([]), marker=MARKER, not_before=BOUNDARY)
    assert (result.found, result.conclusion) == (False, "exhausted")


# ---------------------------------------------------------------------------
# find_marker: inconclusive scans fail closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marker_beyond_the_page_budget_is_inconclusive_not_absent() -> None:
    position = MARKER_SCAN_MAX_PAGES * 100 + 1
    source = PageSource(_thread(position, newer_total=position + 10))

    with pytest.raises(MarkerScanInconclusive) as excinfo:
        await find_marker(source, marker=MARKER, not_before=BOUNDARY)

    assert excinfo.value.reason == "page_budget_exhausted"
    assert len(source.calls) == MARKER_SCAN_MAX_PAGES


def _mutator(target_page: int, change):
    def mutate(page_no: int, response: dict) -> dict:
        return change(response) if page_no == target_page else response

    return mutate


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda r: {**r, "meta": {"has_more": True}}, "missing_cursor"),
        (lambda r: {**r, "meta": {"has_more": True, "after_cursor": ""}}, "missing_cursor"),
        (lambda r: {**r, "meta": {"has_more": True, "after_cursor": "100"}}, "repeated_cursor"),
        (lambda r: {**r, "meta": {}}, "malformed_page"),
        (lambda r: {"meta": r["meta"]}, "malformed_page"),
        (lambda r: {**r, "comments": "nope"}, "malformed_page"),
        (lambda r: {**r, "comments": [*r["comments"][:3], "x"]}, "malformed_comment"),
        (
            lambda r: {**r, "comments": [{**r["comments"][0], "created_at": "garbage"}]},
            "malformed_comment",
        ),
        (
            lambda r: {**r, "comments": list(reversed(r["comments"]))},
            "unordered_comments",
        ),
    ],
    ids=[
        "no-cursor",
        "empty-cursor",
        "repeated-cursor",
        "no-has-more",
        "no-comments-key",
        "comments-not-a-list",
        "comment-not-an-object",
        "bad-timestamp",
        "out-of-order",
    ],
)
@pytest.mark.asyncio
async def test_unusable_pages_are_inconclusive(change, reason) -> None:
    source = PageSource(_thread(None, newer_total=450))
    source.mutate = _mutator(2, change)

    with pytest.raises(MarkerScanInconclusive) as excinfo:
        await find_marker(source, marker=MARKER, not_before=BOUNDARY)

    assert excinfo.value.reason == reason


@pytest.mark.parametrize(
    "error",
    [
        ZendeskAPIError("Zendesk API error 429", status_code=429),
        ZendeskAPIError("Zendesk API error 503", status_code=503),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("refused"),
    ],
    ids=["rate-limited", "server-error", "timeout", "connect-error"],
)
@pytest.mark.asyncio
async def test_provider_errors_propagate_for_the_caller_to_retry(error) -> None:
    source = PageSource(_thread(None, newer_total=300))

    def mutate(page_no, response):
        if page_no == 2:
            raise error
        return response

    source.mutate = mutate
    with pytest.raises(type(error)):
        await find_marker(source, marker=MARKER, not_before=BOUNDARY)


@pytest.mark.asyncio
async def test_next_link_is_an_accepted_continuation() -> None:
    source = PageSource(_thread(150, newer_total=200))

    def link_only(page_no, response):
        meta = dict(response["meta"])
        cursor = meta.pop("after_cursor", None)
        result = {**response, "meta": meta}
        if cursor:
            result["links"] = {
                "next": f"https://x.zendesk.com/api/v2/tickets/1/comments.json?page%5Bafter%5D={cursor}"
            }
        return result

    source.mutate = link_only
    result = await find_marker(source, marker=MARKER, not_before=BOUNDARY)
    assert result.found and result.pages == 2


def test_boundary_allows_for_clock_skew() -> None:
    assert marker_boundary(NOW) == NOW - timedelta(minutes=10)
    naive = datetime(2026, 10, 12, 12, 0)  # noqa: DTZ001 - naive on purpose
    assert marker_boundary(naive).tzinfo is not None


# ---------------------------------------------------------------------------
# Full delivery flow (FakeZendesk + PostgreSQL)
# ---------------------------------------------------------------------------


async def _accept_then_die_then_recover(db, org_id, zendesk, monkeypatch, *, newer: int):
    """Provider accepts the reply; the process dies before any local commit;
    ``newer`` comments land on the ticket after it; the job is recovered."""
    case = await harness._zendesk_reply(db, org_id)
    await harness._provider_accepts_then_process_dies(db, case, zendesk, monkeypatch)
    zendesk.add_newer(case["zendesk_ticket_id"], newer)
    return case


@pytest.mark.parametrize("newer", [0, 49, 50, 99, 100, 249])
@pytest.mark.asyncio
async def test_recovery_finds_the_marker_at_any_depth_without_resending(
    db, org_scope, zendesk, monkeypatch, newer
):
    """Marker positions 1, 50, 51, 100, 101 and 250 (``newer`` + 1)."""
    org_id = await org_scope()
    case = await _accept_then_die_then_recover(db, org_id, zendesk, monkeypatch, newer=newer)

    await harness._run_job(db, case["job_id"])

    assert zendesk.sends == 1, f"duplicate reply with the marker at position {newer + 1}"
    assert zendesk.marker_count(case["zendesk_ticket_id"], case["token"]) == 1
    message = await harness._fresh(ConversationMessage, case["message_id"])
    assert message.delivery_status == "sent"
    assert message.external_message_id is not None
    assert (await harness._fresh(IntegrationJob, case["job_id"])).status == "completed"


@pytest.mark.asyncio
async def test_old_history_does_not_cost_pages(db, org_scope, zendesk) -> None:
    """5,000 comments from before the reply: one page proves absence, then the
    reply is sent once."""
    org_id = await org_scope()
    case = await harness._zendesk_reply(db, org_id)
    zendesk.seed(case["zendesk_ticket_id"], 5000)

    await harness._run_job(db, case["job_id"])

    assert zendesk.sends == 1
    assert zendesk.page_calls <= 3  # pre-send scan + post-send id lookup


@pytest.mark.asyncio
async def test_inconclusive_scan_is_terminal_and_never_sends(db, org_scope, zendesk, monkeypatch):
    """More newer comments than the bounded scan covers: the reply is NOT
    resent; the job fails terminally with an operator-actionable category."""
    org_id = await org_scope()
    case = await _accept_then_die_then_recover(
        db, org_id, zendesk, monkeypatch, newer=MARKER_SCAN_MAX_PAGES * 100
    )

    result = await worker.process_claimed_job(
        db, await harness._claim_specific(db, case["job_id"])
    )

    assert zendesk.sends == 1, "an inconclusive scan must never send"
    assert result.outcome == "failed"
    job = await harness._fresh(IntegrationJob, case["job_id"])
    message = await harness._fresh(ConversationMessage, case["message_id"])
    assert job.status == "failed"
    assert message.delivery_status == "failed"
    assert message.delivery_error_code == "delivery_unverified"


@pytest.mark.parametrize(
    ("knob", "expected_job", "expected_code"),
    [
        ("drop_cursor", "failed", "delivery_unverified"),
        ("repeat_cursor", "failed", "delivery_unverified"),
        ("malformed", "failed", "delivery_unverified"),
        ("rate_limit", "retry", "provider_unavailable"),
        ("timeout", "retry", "provider_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_scan_faults_on_a_deep_page_never_send(
    db, org_scope, zendesk, monkeypatch, knob, expected_job, expected_code
):
    org_id = await org_scope()
    case = await _accept_then_die_then_recover(db, org_id, zendesk, monkeypatch, newer=250)
    if knob == "drop_cursor":
        zendesk.drop_cursor_on_page = 2
    elif knob == "repeat_cursor":
        zendesk.repeat_cursor_on_page = 2
    elif knob == "malformed":
        zendesk.malformed_on_page = 2
    elif knob == "rate_limit":
        zendesk.page_errors[2] = ZendeskAPIError("Zendesk API error 429", status_code=429)
    else:
        zendesk.page_errors[2] = httpx.ReadTimeout("timed out")

    await harness._run_job(db, case["job_id"])

    assert zendesk.sends == 1, "a faulty scan must never send"
    job = await harness._fresh(IntegrationJob, case["job_id"])
    message = await harness._fresh(ConversationMessage, case["message_id"])
    assert job.status == expected_job
    assert message.delivery_error_code == expected_code


@pytest.mark.asyncio
async def test_comments_arriving_during_recovery_scan(db, org_scope, zendesk, monkeypatch):
    org_id = await org_scope()
    case = await _accept_then_die_then_recover(db, org_id, zendesk, monkeypatch, newer=150)
    zendesk.arrivals_after_page[1] = 40  # 40 new comments land mid-scan

    await harness._run_job(db, case["job_id"])

    assert zendesk.sends == 1
    assert (await harness._fresh(ConversationMessage, case["message_id"])).delivery_status == "sent"


@pytest.mark.asyncio
async def test_first_delivery_still_sends_exactly_once(db, org_scope, zendesk):
    org_id = await org_scope()
    case = await harness._zendesk_reply(db, org_id)
    zendesk.add_newer(case["zendesk_ticket_id"], 300)  # busy ticket, no marker

    await harness._run_job(db, case["job_id"])

    assert zendesk.sends == 1
    assert zendesk.marker_count(case["zendesk_ticket_id"], case["token"]) == 1


# ---------------------------------------------------------------------------
# Agent execution uses the same verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_marker_check_is_complete_and_fails_closed(monkeypatch) -> None:
    from app.services.agent_execution_service import (
        AgentExecutionStateError,
        agent_execution_service,
    )

    run_id = "run-abc"
    marker = agent_execution_service._comment_marker(run_id)
    deep = [
        {"id": n, "body": marker if n == 260 else "x", "created_at": _stamp(NOW + timedelta(minutes=900 - n))}
        for n in range(1, 400)
    ]

    async def pages(self, db, ticket_id, *, organization_id, cursor=None, page_size=100):
        return await PageSource(deep)(cursor)

    monkeypatch.setattr(ZendeskClient, "get_ticket_comments_page", pages)
    assert await agent_execution_service._already_written(
        None, zendesk_ticket_id=1, run_id=run_id, organization_id=1, not_before=BOUNDARY
    )

    async def broken(self, db, ticket_id, *, organization_id, cursor=None, page_size=100):
        return {"comments": [{"id": 1, "body": "x", "created_at": _stamp(NOW)}], "meta": {"has_more": True}}

    monkeypatch.setattr(ZendeskClient, "get_ticket_comments_page", broken)
    with pytest.raises(AgentExecutionStateError):
        await agent_execution_service._already_written(
            None, zendesk_ticket_id=1, run_id=run_id, organization_id=1, not_before=BOUNDARY
        )

