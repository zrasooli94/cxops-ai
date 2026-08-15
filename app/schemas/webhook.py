from pydantic import BaseModel, Field


class TicketEventWebhook(BaseModel):
    event_id: str = Field(
        min_length=1,
        max_length=255,
    )

    event_type: str = Field(
        min_length=1,
        max_length=100,
    )

    source: str = Field(
        default="external",
        max_length=50,
    )

    ticket_id: int | None = None

    payload: dict = Field(
        default_factory=dict,
    )


class WebhookReceipt(BaseModel):
    event_id: str
    stored_event_id: int
    duplicate: bool
    status: str