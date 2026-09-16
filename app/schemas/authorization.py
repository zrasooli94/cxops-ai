from pydantic import BaseModel


class AuthorizationInfo(BaseModel):
    """Safe authorization info returned by the proof endpoint.

    Contains no JWT claims, tokens, or internal secrets.
    """

    organization_id: int
    role: str
    capabilities: list[str]
