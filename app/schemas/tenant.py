from pydantic import BaseModel


class TenantInfo(BaseModel):
    """Safe tenant info deliberately lacking subject/identity claims."""

    organization_id: int
    organization_name: str
