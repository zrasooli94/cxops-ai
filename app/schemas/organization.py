from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    industry: str | None = None
    external_id: str | None = None


class OrganizationRead(BaseModel):
    id: int
    name: str
    industry: str | None
    external_id: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)