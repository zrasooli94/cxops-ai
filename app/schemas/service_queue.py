from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServiceQueueCreate(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    active: bool = True
    is_default: bool = False
    sla_policy_id: int | None = None

    @field_validator("key")
    @classmethod
    def _lowercase_key(cls, value: str) -> str:
        return value.strip().lower()


class ServiceQueueUpdate(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    active: bool | None = None
    is_default: bool | None = None
    sla_policy_id: int | None = None

    @field_validator("key")
    @classmethod
    def _lowercase_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()


class ServiceQueueRead(BaseModel):
    id: int
    organization_id: int
    key: str
    name: str
    description: str | None
    active: bool
    is_default: bool
    sla_policy_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ServiceQueueListItem(BaseModel):
    id: int
    key: str
    name: str
    active: bool
    is_default: bool
    sla_policy_id: int | None
