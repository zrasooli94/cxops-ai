"""Bounded, verifiable search for an exactly-once marker in Zendesk comments.

Before sending a reply (or executing an agent tool plan) we must know whether
a previous attempt already wrote it to Zendesk. The previous implementation
looked at a single page, so a marker pushed beyond that page by newer comments
was invisible and the reply could be sent twice.

This scanner walks Zendesk's **cursor** pagination for ticket comments
(``page[size]=100``, ``sort=-created_at``: newest first; cursor pagination has
no depth limit, unlike offset pagination's 100-page/10,000-record cap) and
reaches a conclusion only in one of three provable ways:

* the marker is found (``found``);
* the scan reached a comment created before ``not_before`` - the earliest
  moment the marker could have been written (the reply's creation time minus
  a clock-skew margin) - so every remaining comment is too old to carry it
  (``reached_boundary``);
* Zendesk reports no further pages (``exhausted``).

Anything else is **inconclusive** and raises :class:`MarkerScanInconclusive`
with a bounded reason: the page budget ran out, the continuation cursor was
missing or repeated, a page or comment was malformed, or comments were not in
the requested order. Callers must fail closed on it - never send on "probably
not there". Provider/transport errors are raised unchanged for the caller to
classify as retryable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

MARKER_SCAN_PAGE_SIZE = 100
# At most this many pages (100 comments each) NEWER than the boundary are
# examined. 20 pages = 2,000 comments created after the reply was queued.
MARKER_SCAN_MAX_PAGES = 20
# Our clock (message creation) and Zendesk's clock (comment creation) differ;
# the boundary is moved back by this margin so skew cannot hide a marker.
MARKER_SCAN_CLOCK_SKEW = timedelta(minutes=10)

INCONCLUSIVE_REASONS = frozenset(
    {
        "page_budget_exhausted",
        "missing_cursor",
        "repeated_cursor",
        "malformed_page",
        "malformed_comment",
        "unordered_comments",
    }
)

FetchPage = Callable[[str | None], Awaitable[dict[str, Any]]]


class MarkerScanInconclusive(Exception):
    """The scan could not establish whether the marker exists. Fail closed."""

    def __init__(self, reason: str, *, pages: int) -> None:
        if reason not in INCONCLUSIVE_REASONS:
            raise ValueError(f"unknown inconclusive reason: {reason}")
        super().__init__(f"marker scan inconclusive: {reason} after {pages} page(s)")
        self.reason = reason
        self.pages = pages


@dataclass(frozen=True)
class MarkerScanResult:
    found: bool
    comment_id: str | None
    pages: int
    conclusion: str  # "found" | "reached_boundary" | "exhausted"


def marker_boundary(created_at: datetime) -> datetime:
    """Earliest creation time a marker for something created at ``created_at``
    could carry, after allowing for clock skew."""
    moment = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    return moment - MARKER_SCAN_CLOCK_SKEW


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _next_cursor(response: dict[str, Any]) -> str | None:
    meta = response.get("meta")
    if isinstance(meta, dict):
        cursor = meta.get("after_cursor")
        if isinstance(cursor, str) and cursor:
            return cursor
    links = response.get("links")
    if isinstance(links, dict) and isinstance(links.get("next"), str):
        values = parse_qs(urlsplit(links["next"]).query).get("page[after]")
        if values and values[0]:
            return values[0]
    return None


async def find_marker(
    fetch_page: FetchPage,
    *,
    marker: str,
    not_before: datetime,
    max_pages: int = MARKER_SCAN_MAX_PAGES,
    public_only: bool = False,
) -> MarkerScanResult:
    """Search newest-first pages for ``marker``. See the module docstring.

    ``public_only``: only a PUBLIC comment counts as the marker. A customer
    reply is always written public, so a private note that merely quotes the
    marker (e.g. an agent pasting it) must not be mistaken for delivery.
    """
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for page in range(1, max_pages + 1):
        response = await fetch_page(cursor)
        comments = response.get("comments") if isinstance(response, dict) else None
        if not isinstance(comments, list):
            raise MarkerScanInconclusive("malformed_page", pages=page)

        previous: datetime | None = None
        oldest: datetime | None = None
        for comment in comments:
            if not isinstance(comment, dict):
                raise MarkerScanInconclusive("malformed_comment", pages=page)
            body = comment.get("body")
            if (
                isinstance(body, str)
                and marker in body
                and (not public_only or comment.get("public") is True)
            ):
                comment_id = comment.get("id")
                return MarkerScanResult(
                    found=True,
                    comment_id=str(comment_id) if comment_id is not None else None,
                    pages=page,
                    conclusion="found",
                )
            created = _parse_time(comment.get("created_at"))
            if created is None:
                raise MarkerScanInconclusive("malformed_comment", pages=page)
            if previous is not None and created > previous:
                raise MarkerScanInconclusive("unordered_comments", pages=page)
            previous = oldest = created

        if oldest is not None and oldest < not_before:
            return MarkerScanResult(False, None, page, "reached_boundary")

        meta = response.get("meta")
        has_more = meta.get("has_more") if isinstance(meta, dict) else None
        if has_more is False:
            return MarkerScanResult(False, None, page, "exhausted")
        if has_more is not True:
            raise MarkerScanInconclusive("malformed_page", pages=page)

        next_cursor = _next_cursor(response)
        if next_cursor is None:
            raise MarkerScanInconclusive("missing_cursor", pages=page)
        if next_cursor in seen_cursors or next_cursor == cursor:
            raise MarkerScanInconclusive("repeated_cursor", pages=page)
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    raise MarkerScanInconclusive("page_budget_exhausted", pages=max_pages)
