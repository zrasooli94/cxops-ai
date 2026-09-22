from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SLAState = Literal["not_configured", "on_track", "due_soon", "breached", "met"]


class OperationsQueueItem(BaseModel):
    ticket_id: int
    conversation_id: int | None
    subject: str
    status: str
    priority: str
    category: str | None
    service_queue_id: int | None
    service_queue_name: str | None
    assigned_subject: str | None
    is_assigned_to_me: bool
    created_at: datetime
    updated_at: datetime
    needs_response: bool
    first_response_due_at: datetime | None
    first_response_at: datetime | None
    first_response_sla_state: SLAState
    resolution_due_at: datetime | None
    resolved_at: datetime | None
    resolution_sla_state: SLAState
    overall_sla_state: SLAState
    routing_source: str | None
    ai_routing_suggestion: dict | None


class OperationsQueueParams(BaseModel):
    queue_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    priority: str | None = None
    sla_state: SLAState | None = None
    needs_response: bool | None = None
    unassigned: bool | None = None
    mine: bool | None = None
    search: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class OperationsSummaryQueueCount(BaseModel):
    id: int
    name: str
    key: str
    count: int


class OperationsSummary(BaseModel):
    open: int
    unassigned: int
    needs_response: int
    response_breaches: int
    resolution_breaches: int
    due_soon: int
    urgent: int
    high: int
    by_queue: list[OperationsSummaryQueueCount]


class AIRoutingSuggestionApply(BaseModel):
    queue_id: int = Field(ge=1)
