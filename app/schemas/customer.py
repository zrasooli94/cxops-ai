from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    phone: str | None = None
    external_id: str | None = None

    # organization_id is deliberately not accepted from the payload: the tenant
    # boundary always comes from the resolved CurrentTenant.
    model_config = ConfigDict(extra="forbid")


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    phone: str | None = None
    external_id: str | None = None

    # Reject unknown fields (e.g. organization_id) so the tenant boundary can
    # never be influenced by client payloads.
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "email")
    @classmethod
    def _not_null(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class CustomerRead(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None
    organization_id: int
    external_id: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CustomerListResponse(BaseModel):
    items: list[CustomerRead]
    total: int
    offset: int
    limit: int


class CustomerTicketSummary(BaseModel):
    """Compact, request-scoped ticket shape for the customer 360 view.

    Deliberately omits ``description`` and requester email to keep the list
    payload small; ``organization_id`` is omitted because the caller is already
    tenant-scoped.
    """

    id: int
    external_id: str | None
    subject: str
    status: str
    priority: str
    source: str
    category: str | None
    assigned_team: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CustomerTicketListResponse(BaseModel):
    items: list[CustomerTicketSummary]
    total: int
    offset: int
    limit: int


class CustomerSummary(BaseModel):
    """Safe derived service metrics for a customer.

    ``latest_ticket_at`` is the most recent ticket creation; ``latest_interaction_at``
    is the most recent ticket update. Both derive from the ticket aggregate only —
    no derived vocabulary is invented.
    """

    customer_id: int
    total_tickets: int
    open_tickets: int
    closed_or_resolved_tickets: int
    latest_ticket_at: datetime | None
    latest_interaction_at: datetime | None
    most_recent_ticket: CustomerTicketSummary | None
    common_category: str | None
