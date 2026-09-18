from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

CustomerTimelineSource = Literal["ticket", "ticket_event", "agent"]


class CustomerTimelineEvent(BaseModel):
    """One allowlisted activity entry in a customer's timeline.

    ``metadata`` carries only safe, allowlisted scalar fields per source. Raw
    webhook payloads (``TicketEvent.payload``) and agent internals (``reason``,
    ``response_draft``, ``tool_plan``, ``sources``, ``workflow_path``,
    ``reviewer_note``, ``error_message``) are never exposed here.
    """

    id: str
    type: str
    source: CustomerTimelineSource
    occurred_at: datetime
    title: str
    summary: str | None = None
    ticket_id: int | None = None
    agent_run_id: str | None = None
    metadata: dict[str, Any] = {}


class CustomerTimelineResponse(BaseModel):
    items: list[CustomerTimelineEvent]
    total: int
    partial: bool
    unavailable_sources: list[str]