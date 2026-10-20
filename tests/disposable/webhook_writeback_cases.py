"""Fail-closed webhook writeback recovery tests: NO live network, NO production.

The executable path is only imported by the disposable CI workflow, which
requires a precise 127.0.0.1 PostgreSQL URL, synthetic opt-in, and GitHub
Actions. External Zendesk APIs and repositories are replaced with inert
in-memory fakes. The REAL ZendeskWebhookService.process and shared cursor
marker scanner run unchanged.
"""
from __future__ import annotations

import os

from scripts.worker_disposable_guard import require_disposable_environment

require_disposable_environment(os.environ)

import asyncio
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.integrations.zendesk.client import ZendeskAPIError
from app.models.ticket_event import TicketEvent
from app.repositories.ticket_event_repository import TicketEventRepository
from app.repositories.ticket_repository import TicketRepository
from app.services import zendesk_webhook_service as webhook
from app.services.automation_service import AutomationService
from app.services.conversation_ingestion_service import ConversationIngestionService
from app.services.zendesk_sync_service import ZendeskSyncService

TENANT_ID = 17
TICKET_ID = 4242
INVOCATION = "webhook-safe-event-9e4b"
MARKER = f"CXOps Event: {INVOCATION}"


class FakeWritebackProvider:
    """Newest-first cursor comments, plus deliberately truncated ingestion."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.event_created_at = self.now - timedelta(minutes=2)
        self.comments: list[dict] = []
        self.next_id = 1
        self.event: TicketEvent | None = None
        self.ticket = SimpleNamespace(
            id=222,
            external_id=str(TICKET_ID),
            organization_id=TENANT_ID,
            category="support",
            assigned_team="staff",
            priority="normal",
        )
        self.page_calls: list[str | None] = []
        self.ingestion_snapshots: list[list[dict]] = []
        self.writes = 0
        self.completions = 0
        self.fail_after_accept = False
        self.fault_page: int | None = None
        self.fault_kind: str | None = None
        self.network_exception: Exception | None = None

    def _append(self, body: str, *, public: bool, created: datetime) -> None:
        self.comments.append({
            "id": self.next_id,
            "body": body,
            "public": public,
            "created_at": created.isoformat(),
        })
        self.next_id += 1

    def old_history(self, count: int = 300) -> None:
        origin = self.now - timedelta(days=2)
        for index in range(count):
            self._append(
                "historical unrelated note",
                public=False,
                created=origin + timedelta(seconds=index),
            )

    def newer_comments(self, count: int) -> None:
        for index in range(count):
            self._append(
                "later unrelated comment",
                public=True,
                created=self.now + timedelta(minutes=5, seconds=index),
            )

    def existing_writeback(self) -> None:
        self._append(
            f"CXOps AI automation result\nPriority: normal\n{MARKER}",
            public=False,
            created=self.now,
        )

    def newest_first(self) -> list[dict]:
        return sorted(
            self.comments,
            key=lambda c: (c["created_at"], c["id"]),
            reverse=True,
        )

    async def get_ticket_comments_page(
        self,
        db: object,
        ticket_id: int,
        *,
        organization_id: int,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> dict:
        assert ticket_id == TICKET_ID and organization_id == TENANT_ID
        assert page_size == 100
        self.page_calls.append(cursor)
        page_number = len(self.page_calls)
        if self.fault_page == page_number and self.network_exception is not None:
            raise self.network_exception

        start = 0 if cursor is None else int(cursor)
        ordered = self.newest_first()
        page = [dict(c) for c in ordered[start : start + page_size]]
        more = start + page_size < len(ordered)
        meta: dict = {"has_more": more}
        if more:
            meta["after_cursor"] = str(start + page_size)

        if page_number == self.fault_page:
            if self.fault_kind == "missing_cursor":
                meta.pop("after_cursor", None)
            elif self.fault_kind == "repeated_cursor":
                meta["after_cursor"] = cursor
            elif self.fault_kind == "malformed_page":
                return {"comments": "not a list", "meta": meta}
            elif self.fault_kind == "unordered_cross_page" and page:
                page[0]["created_at"] = (
                    self.now + timedelta(days=1)
                ).isoformat()

        return {"comments": page, "meta": meta}

    async def update_ticket(
        self, *, db: object, ticket_id: int, changes: dict, organization_id: int
    ) -> dict:
        assert ticket_id == TICKET_ID and organization_id == TENANT_ID
        assert changes["priority"] == "normal"
        comment = changes["comment"]
        assert comment["public"] is False
        assert any(line == MARKER for line in comment["body"].splitlines())
        self.writes += 1
        self._append(
            comment["body"], public=False, created=self.now
        )
        if self.fail_after_accept:
            self.fail_after_accept = False
            raise RuntimeError("inert provider accepted, local acknowledgment lost")
        return {"ticket": {"id": TICKET_ID}}


@pytest.fixture
def fake_writeback(monkeypatch: pytest.MonkeyPatch) -> FakeWritebackProvider:
    fake = FakeWritebackProvider()

    async def _forbid_network(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected external HTTP request in disposable test")

    monkeypatch.setattr(httpx.AsyncClient, "request", _forbid_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", _forbid_network)

    async def fetch_event(*, db: object, event_key: str) -> TicketEvent | None:
        assert event_key == INVOCATION
        return fake.event

    async def insert_event(*, db: object, event: TicketEvent) -> TicketEvent:
        assert fake.event is None and event.event_key == INVOCATION
        assert event.ticket_id == fake.ticket.id
        event.id = 779
        event.created_at = fake.event_created_at
        event.writeback_completed = False
        fake.event = event
        return event

    async def complete_event(*, db: object, event: TicketEvent) -> TicketEvent:
        assert fake.event is event and not event.writeback_completed
        event.writeback_completed = True
        event.writeback_at = datetime.now(UTC)
        fake.completions += 1
        return event

    async def sync_ticket(
        *, db: object, zendesk_ticket_id: int, organization_id: int
    ) -> SimpleNamespace:
        assert zendesk_ticket_id == TICKET_ID and organization_id == TENANT_ID
        return fake.ticket

    async def read_ticket(
        *, db: object, ticket_id: int, organization_id: int
    ) -> SimpleNamespace:
        assert ticket_id == fake.ticket.id and organization_id == TENANT_ID
        return fake.ticket

    async def automate(
        *, db: object, event: TicketEvent, organization_id: int
    ) -> None:
        assert event is fake.event and organization_id == TENANT_ID

    async def ingest(
        db: object, *, zendesk_ticket_id: int, organization_id: int
    ) -> tuple[object, dict]:
        assert zendesk_ticket_id == TICKET_ID and organization_id == TENANT_ID
        # This deliberately mirrors the CURRENT ingestion endpoint's
        # oldest-first at-most-100 comments, demonstrating that it is NOT a
        # sufficient source of truth for deciding a writeback is absent.
        oldest = list(reversed(fake.newest_first()))[:100]
        fake.ingestion_snapshots.append(oldest)
        return object(), {"comments": oldest}

    monkeypatch.setattr(
        TicketEventRepository, "get_by_event_key", staticmethod(fetch_event)
    )
    monkeypatch.setattr(TicketEventRepository, "create", staticmethod(insert_event))
    monkeypatch.setattr(
        TicketEventRepository, "mark_writeback_completed", staticmethod(complete_event)
    )
    monkeypatch.setattr(
        ZendeskSyncService, "sync_ticket_for_tenant", staticmethod(sync_ticket)
    )
    monkeypatch.setattr(
        TicketRepository, "get_by_id_for_tenant", staticmethod(read_ticket)
    )
    monkeypatch.setattr(
        AutomationService, "process_ticket_event", staticmethod(automate)
    )
    monkeypatch.setattr(
        ConversationIngestionService, "ingest_zendesk_ticket", staticmethod(ingest)
    )
    monkeypatch.setattr(
        webhook.zendesk_client, "get_ticket_comments_page",
        fake.get_ticket_comments_page,
    )
    monkeypatch.setattr(webhook.zendesk_client, "update_ticket", fake.update_ticket)
    return fake


async def _process() -> object:
    return await webhook.ZendeskWebhookService.process(
        object(),
        invocation_id=INVOCATION,
        event_type="ticket.updated",
        zendesk_ticket_id=TICKET_ID,
        payload={"synthetic": True},
        organization_id=TENANT_ID,
    )


@pytest.mark.asyncio
async def test_first_writeback_and_duplicate_short_circuit(
    fake_writeback: FakeWritebackProvider,
) -> None:
    first = await _process()
    assert first.status == "processed"
    assert fake_writeback.writes == fake_writeback.completions == 1
    before = len(fake_writeback.page_calls)
    again = await _process()
    assert again.status == "already_processed" and again.duplicate is True
    assert len(fake_writeback.page_calls) == before
    assert fake_writeback.writes == 1


@pytest.mark.asyncio
async def test_replay_after_accepted_write_and_lost_sql_ack_uses_deep_marker(
    fake_writeback: FakeWritebackProvider,
) -> None:
    fake_writeback.old_history(300)
    fake_writeback.fail_after_accept = True
    with pytest.raises(RuntimeError, match="inert provider accepted"):
        await _process()

    assert fake_writeback.writes == 1
    assert fake_writeback.event is not None
    assert fake_writeback.event.writeback_completed is False

    fake_writeback.newer_comments(250)
    # Original oldest-first ingestion's first 100 comments MISSED the marker.
    oldest = list(reversed(fake_writeback.newest_first()))[:100]
    assert all(MARKER not in entry["body"] for entry in oldest)

    fake_writeback.page_calls.clear()
    recovered = await _process()
    assert recovered.status == "processed"
    assert fake_writeback.writes == 1, "must NOT re-issue a private note"
    assert len(fake_writeback.page_calls) == 3
    assert fake_writeback.event.writeback_completed is True
    assert fake_writeback.completions == 1
    assert all(MARKER not in c["body"] for c in fake_writeback.ingestion_snapshots[-1])


@pytest.mark.parametrize("position", [1, 100, 101, 250, 1999])
@pytest.mark.asyncio
async def test_existing_private_note_at_depth_never_resends(
    fake_writeback: FakeWritebackProvider, position: int
) -> None:
    fake_writeback.old_history(300)
    fake_writeback.existing_writeback()
    fake_writeback.newer_comments(position - 1)
    result = await _process()
    assert result.status == "processed"
    assert fake_writeback.writes == 0
    assert fake_writeback.completions == 1
    assert len(fake_writeback.page_calls) == math.ceil(position / 100)


@pytest.mark.asyncio
async def test_absence_is_proved_by_old_boundary_and_writeback_sent_once(
    fake_writeback: FakeWritebackProvider,
) -> None:
    fake_writeback.old_history(300)
    fake_writeback.newer_comments(115)
    result = await _process()
    assert result.status == "processed"
    assert fake_writeback.writes == 1
    assert fake_writeback.completions == 1
    assert len(fake_writeback.page_calls) == 2


@pytest.mark.parametrize(
    "fault_kind",
    ["missing_cursor", "repeated_cursor", "malformed_page", "unordered_cross_page"],
)
@pytest.mark.asyncio
async def test_ambiguous_pagination_never_authorizes_new_write(
    fake_writeback: FakeWritebackProvider, fault_kind: str
) -> None:
    fake_writeback.old_history(300)
    fake_writeback.newer_comments(250)
    fake_writeback.fault_page = 2
    fake_writeback.fault_kind = fault_kind
    with pytest.raises(webhook.WebhookWritebackUnverified):
        await _process()
    assert fake_writeback.writes == 0
    assert fake_writeback.completions == 0
    assert fake_writeback.event is not None
    assert fake_writeback.event.writeback_completed is False


@pytest.mark.parametrize(
    "error",
    [
        ZendeskAPIError("synthetic rate limit", status_code=429),
        httpx.ReadTimeout("synthetic timeout"),
    ],
    ids=["zendesk-429", "provider-timeout"],
)
@pytest.mark.asyncio
async def test_provider_lookup_failure_keeps_job_unacknowledged_without_write(
    fake_writeback: FakeWritebackProvider, error: Exception
) -> None:
    fake_writeback.old_history(300)
    fake_writeback.newer_comments(250)
    fake_writeback.fault_page = 2
    fake_writeback.network_exception = error
    with pytest.raises(type(error)):
        await _process()
    assert fake_writeback.writes == 0
    assert fake_writeback.completions == 0
    assert fake_writeback.event is not None
    assert fake_writeback.event.writeback_completed is False


@pytest.mark.parametrize(
    ("body", "public"),
    [
        (MARKER, True),
        (f"CXOps Event: {INVOCATION}-another-event", False),
        (f"Quoted in another note: {MARKER}", False),
    ],
    ids=["public-quote", "id-prefix-collision", "embedded-private-quote"],
)
@pytest.mark.asyncio
async def test_spoofed_or_partial_marker_is_not_proof_of_internal_write(
    fake_writeback: FakeWritebackProvider, body: str, public: bool
) -> None:
    fake_writeback._append(body, public=public, created=fake_writeback.now)
    result = await _process()
    assert result.status == "processed"
    assert fake_writeback.writes == 1
    assert fake_writeback.completions == 1
    private_exact = [
        c for c in fake_writeback.comments
        if c["public"] is False and any(
            line.strip() == MARKER for line in c["body"].splitlines()
        )
    ]
    assert len(private_exact) == 1


@pytest.mark.asyncio
async def test_page_budget_exhaustion_is_explicitly_unverified(
    fake_writeback: FakeWritebackProvider,
) -> None:
    fake_writeback.old_history(300)
    fake_writeback.newer_comments(2100)
    with pytest.raises(webhook.WebhookWritebackUnverified):
        await _process()
    assert len(fake_writeback.page_calls) == 20
    assert fake_writeback.writes == fake_writeback.completions == 0


@pytest.mark.asyncio
async def test_aggregate_scan_deadline_fails_closed(
    fake_writeback: FakeWritebackProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_writeback.old_history(300)
    fake_writeback.newer_comments(250)

    async def _unresponsive_page(
        db: object,
        ticket_id: int,
        *,
        organization_id: int,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> dict:
        assert ticket_id == TICKET_ID and organization_id == TENANT_ID
        await asyncio.Event().wait()
        raise AssertionError("unreachable after cancellation")

    monkeypatch.setattr(
        webhook.zendesk_client, "get_ticket_comments_page", _unresponsive_page
    )
    monkeypatch.setattr(webhook, "WEBHOOK_MARKER_SCAN_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(webhook.WebhookWritebackUnverified, match="timed out"):
        await asyncio.wait_for(_process(), timeout=2)
    assert fake_writeback.writes == 0
    assert fake_writeback.completions == 0
    assert fake_writeback.event is not None
    assert fake_writeback.event.writeback_completed is False


@pytest.mark.parametrize("visibility", [None, "false"], ids=["missing", "not-a-boolean"])
@pytest.mark.asyncio
async def test_matched_marker_with_unknown_visibility_stops_writeback(
    fake_writeback: FakeWritebackProvider, visibility: object
) -> None:
    fake_writeback.old_history(300)
    fake_writeback.existing_writeback()
    fake_writeback.comments[-1]["public"] = visibility
    with pytest.raises(webhook.WebhookWritebackUnverified):
        await _process()
    assert fake_writeback.writes == 0
    assert fake_writeback.completions == 0
    assert fake_writeback.event is not None
    assert fake_writeback.event.writeback_completed is False
