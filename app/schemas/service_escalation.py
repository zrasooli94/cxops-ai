from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EscalationListItem(BaseModel):
    id: int
    ticket_id: int
    conversation_id: int | None = None

    subject: str
    priority: str
    status: str

    service_queue_id: int | None = None
    service_queue_name: str | None = None
    assigned_subject: str | None = None

    milestone: str
    stage: str
    due_at: datetime | None = None
    triggered_at: datetime

    acknowledged_at: datetime | None = None
    acknowledged_by_subject: str | None = None

    resolved_at: datetime | None = None
    resolution_reason: str | None = None

    resolution_sla_cycle: int

    model_config = ConfigDict(from_attributes=True)


class MilestoneBucket(BaseModel):
    milestone: str
    triggered_in_window: int
    breached_in_window: int
    due_soon_in_window: int
    acknowledged_in_window: int
    currently_active: int
    currently_unacknowledged: int
    average_acknowledgement_minutes: float | None = None


class StageBucket(BaseModel):
    stage: str
    triggered_in_window: int
    breached_in_window: int
    due_soon_in_window: int
    acknowledged_in_window: int
    currently_active: int
    currently_unacknowledged: int
    average_acknowledgement_minutes: float | None = None


class EscalationWindowedSummary(BaseModel):
    days: int
    triggered_in_window: int
    breached_in_window: int
    due_soon_in_window: int
    acknowledged_in_window: int
    currently_active: int
    currently_unacknowledged: int
    average_acknowledgement_minutes: float | None = None
    by_milestone: list[MilestoneBucket]
    by_stage: list[StageBucket]


class EscalationSummary(BaseModel):
    total: int
    active: int
    unacknowledged: int
    due_soon: int
    breached: int


class EscalationFilters(BaseModel):
    status: str | None = None
    stage: str | None = None
    milestone: str | None = None
    queue_id: int | None = None
    mine: bool = False
    limit: int = 100
    offset: int = 0


class AcknowledgeResponse(BaseModel):
    id: int
    acknowledged_at: datetime | None
    acknowledged_by_subject: str | None
    status: str


class WorkloadMember(BaseModel):
    subject: str
    open_assigned: int
    breached: int
    urgent: int
    needs_response: int
    due_soon: int


class WorkloadResponse(BaseModel):
    members: list[WorkloadMember]
