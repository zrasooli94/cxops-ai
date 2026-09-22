from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)

    requester_email: EmailStr | None = None

    priority: str = "normal"
    source: str = "api"

    external_id: str | None = None

    customer_id: int | None = None


class TicketUpdate(BaseModel):
    subject: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    description: str | None = Field(
        default=None,
        min_length=1,
    )

    requester_email: EmailStr | None = None

    status: (
        Literal[
            "new",
            "open",
            "pending",
            "solved",
            "closed",
        ]
        | None
    ) = None

    priority: (
        Literal[
            "low",
            "normal",
            "high",
            "urgent",
        ]
        | None
    ) = None

    category: str | None = None
    assigned_team: str | None = None

    customer_id: int | None = None

    @field_validator("subject", "description")
    @classmethod
    def _not_null(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class TicketRead(BaseModel):
    id: int

    external_id: str | None
    subject: str
    description: str

    status: str
    priority: str
    requester_email: str | None
    source: str

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )

    category: str | None
    assigned_team: str | None

    customer_id: int | None
    organization_id: int

    service_queue_id: int | None
    assigned_subject: str | None
    sla_policy_id: int | None
    first_response_due_at: datetime | None
    resolution_due_at: datetime | None
    first_response_at: datetime | None
    resolved_at: datetime | None
    routing_source: str | None
    routed_at: datetime | None


class TicketAssignmentUpdate(BaseModel):
    service_queue_id: int | None = None
    assigned_subject: str | None = None
