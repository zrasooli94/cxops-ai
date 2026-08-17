from typing import Literal

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    model_validator,
)


class ZendeskTicketCreate(BaseModel):
    subject: str = Field(
        min_length=1,
        max_length=255,
    )

    comment: str = Field(
        min_length=1,
    )

    requester_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    requester_email: EmailStr | None = None

    priority: Literal[
        "low",
        "normal",
        "high",
        "urgent",
    ] | None = None

    @model_validator(mode="after")
    def validate_requester(self):
        if self.requester_email and not self.requester_name:
            raise ValueError(
                "requester_name is required when requester_email is provided"
            )

        if self.requester_name and not self.requester_email:
            raise ValueError(
                "requester_email is required when requester_name is provided"
            )

        return self

class ZendeskTicketUpdate(BaseModel):
    status: Literal[
        "new",
        "open",
        "pending",
        "hold",
        "solved",
        "closed",
    ] | None = None

    priority: Literal[
        "low",
        "normal",
        "high",
        "urgent",
    ] | None = None

    comment: str | None = None