from datetime import datetime

from pydantic import BaseModel, Field


class CustomerContextSummarySchema(BaseModel):
    total_tickets: int
    open_tickets: int
    resolved_tickets: int
    common_category: str | None
    last_ticket_at: datetime | None
    last_interaction_at: datetime | None


class CustomerContextTicketSchema(BaseModel):
    id: int
    subject: str
    status: str
    priority: str
    category: str | None
    source: str
    created_at: datetime | None
    updated_at: datetime | None


class CustomerContextActivitySchema(BaseModel):
    id: str
    type: str
    source: str
    occurred_at: datetime | None
    title: str


class CustomerContextResponse(BaseModel):
    customer_id: int
    display_name: str
    known_channels: list[str]
    summary: CustomerContextSummarySchema
    recent_tickets: list[CustomerContextTicketSchema]
    recent_activity: list[CustomerContextActivitySchema]
    partial: bool = False
    unavailable_sources: list[str] = Field(default_factory=list)
