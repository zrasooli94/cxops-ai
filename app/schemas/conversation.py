from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ReplyMode = Literal["zendesk", "local_only", "unsupported"]


class ConversationMessageRead(BaseModel):
    id: int
    conversation_id: int
    provider: str
    direction: str
    visibility: str
    body: str
    sent_at: datetime | None
    created_at: datetime
    delivery_status: str | None
    delivered_at: datetime | None
    can_retry: bool = False


class ConversationMessagePreview(BaseModel):
    body: str
    direction: str
    visibility: str
    sent_at: datetime | None


class ConversationListItem(BaseModel):
    id: int
    provider: str
    channel: str
    external_thread_id: str | None
    subject: str | None
    status: str
    customer_id: int | None
    ticket_id: int | None
    latest_message_at: datetime | None
    needs_response: bool
    reply_mode: ReplyMode
    latest_message: ConversationMessagePreview | None


class ConversationListResponse(BaseModel):
    items: list[ConversationListItem]
    total: int
    offset: int
    limit: int


class ConversationDetail(BaseModel):
    id: int
    provider: str
    channel: str
    external_thread_id: str | None
    subject: str | None
    status: str
    customer_id: int | None
    ticket_id: int | None
    created_at: datetime
    updated_at: datetime
    latest_message_at: datetime | None
    needs_response: bool
    reply_mode: ReplyMode


class ConversationSummaryResponse(BaseModel):
    total: int
    open: int
    closed: int
    needs_response: int
    by_provider: dict[str, int] = Field(default_factory=dict)
    by_channel: dict[str, int] = Field(default_factory=dict)


class ConversationReplyRequest(BaseModel):
    body: str
    client_request_id: str

    @field_validator("body")
    @classmethod
    def body_not_blank(cls, value: str) -> str:
        if value is None or str(value).strip() == "":
            raise ValueError("Reply body cannot be empty")
        return value

    @field_validator("client_request_id")
    @classmethod
    def client_request_id_required(cls, value: str) -> str:
        if not value:
            raise ValueError("client_request_id is required")
        return value


class ConversationReplyResponse(BaseModel):
    message_id: int
    delivery_status: str | None
    duplicate: bool
    job_id: int | None