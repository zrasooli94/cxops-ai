from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AutomationRuleCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=255,
    )

    event_type: str = "ticket.created"

    priority: int = Field(
        default=100,
        ge=1,
    )

    enabled: bool = True

    conditions: dict = Field(
        default_factory=dict,
    )

    actions: dict = Field(
        default_factory=dict,
    )


class AutomationRuleUpdate(BaseModel):
    name: str | None = None
    event_type: str | None = None
    priority: int | None = Field(
        default=None,
        ge=1,
    )
    enabled: bool | None = None
    conditions: dict | None = None
    actions: dict | None = None


class AutomationRuleRead(BaseModel):
    id: int
    name: str
    event_type: str
    priority: int
    enabled: bool
    conditions: dict
    actions: dict
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )
