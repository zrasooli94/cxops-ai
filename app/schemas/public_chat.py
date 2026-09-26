from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Bounded public widget display configuration. ``allowed_origins`` is never
# serialized to the widget: it is a backend-only enforcement list.
class PublicChatConfigResponse(BaseModel):
    display_name: str
    welcome_message: str
    enabled: bool
    theme_token: str | None
    max_message_length: int

    model_config = ConfigDict(from_attributes=True)


class PublicChatSessionSummary(BaseModel):
    token: str
    status: str
    expires_at: datetime


class PublicChatSessionCreateResponse(BaseModel):
    config: PublicChatConfigResponse
    session: PublicChatSessionSummary


class PublicChatMessageRead(BaseModel):
    id: int
    direction: str
    body: str
    sent_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PublicChatStateResponse(BaseModel):
    status: str
    messages: list[PublicChatMessageRead]
    expires_at: datetime


class PublicChatSessionCreateRequest(BaseModel):
    public_widget_key: str = Field(min_length=8, max_length=128)


class PublicChatMessageSendRequest(BaseModel):
    client_message_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=10000)

    @field_validator("client_message_id")
    @classmethod
    def _client_message_id_plain(cls, value: str) -> str:
        if any(ch.isspace() for ch in value) or value.startswith(("http:", "https:")):
            raise ValueError("client_message_id must be a contiguous non-URL token")
        return value


class PublicChatMessageSendResponse(BaseModel):
    message_id: int
    reply: str | None
    status: str
    handoff: bool


class PublicChatHumanRequestResponse(BaseModel):
    status: str
    reply: str


class PublicChatCloseResponse(BaseModel):
    status: str