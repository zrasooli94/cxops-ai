from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    phone: str | None = None
    external_id: str | None = None


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    phone: str | None = None
    external_id: str | None = None

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
