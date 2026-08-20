from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    phone: str | None = None
    organization_id: int | None = None
    external_id: str | None = None


class CustomerRead(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None
    organization_id: int | None
    external_id: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
