"""Staff-facing public-chat handoff schemas (Phase 1P.2)."""

from datetime import datetime

from pydantic import BaseModel


class StaffHandoffSessionSummary(BaseModel):
    session_id: int
    conversation_id: int
    ticket_id: int
    customer_id: int | None
    status: str
    assigned_to_subject: str | None
    assigned_at: datetime | None
    released_at: datetime | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class StaffHandoffSessionsResponse(BaseModel):
    sessions: list[StaffHandoffSessionSummary]


class StaffBusinessActionSummary(BaseModel):
    id: int
    request_type: str
    status: str
    reference_id: str | None
    summary: str | None
    created_at: datetime
    updated_at: datetime


class StaffBusinessActionsResponse(BaseModel):
    actions: list[StaffBusinessActionSummary]