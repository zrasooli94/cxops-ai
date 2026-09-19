from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CustomerIdentityRead(BaseModel):
    id: int
    provider: str
    identity_type: str
    identifier: str
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class CustomerIdentityListResponse(BaseModel):
    items: list[CustomerIdentityRead]
    total: int
    offset: int
    limit: int
