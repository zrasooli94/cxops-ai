from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SLAPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    is_default: bool = False

    first_response_low_minutes: int = Field(ge=1)
    first_response_normal_minutes: int = Field(ge=1)
    first_response_high_minutes: int = Field(ge=1)
    first_response_urgent_minutes: int = Field(ge=1)

    resolution_low_minutes: int = Field(ge=1)
    resolution_normal_minutes: int = Field(ge=1)
    resolution_high_minutes: int = Field(ge=1)
    resolution_urgent_minutes: int = Field(ge=1)


class SLAPolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    is_default: bool | None = None

    first_response_low_minutes: int | None = Field(default=None, ge=1)
    first_response_normal_minutes: int | None = Field(default=None, ge=1)
    first_response_high_minutes: int | None = Field(default=None, ge=1)
    first_response_urgent_minutes: int | None = Field(default=None, ge=1)

    resolution_low_minutes: int | None = Field(default=None, ge=1)
    resolution_normal_minutes: int | None = Field(default=None, ge=1)
    resolution_high_minutes: int | None = Field(default=None, ge=1)
    resolution_urgent_minutes: int | None = Field(default=None, ge=1)


class SLAPolicyRead(BaseModel):
    id: int
    organization_id: int
    name: str
    enabled: bool
    is_default: bool

    first_response_low_minutes: int
    first_response_normal_minutes: int
    first_response_high_minutes: int
    first_response_urgent_minutes: int

    resolution_low_minutes: int
    resolution_normal_minutes: int
    resolution_high_minutes: int
    resolution_urgent_minutes: int

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
