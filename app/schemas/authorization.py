from pydantic import BaseModel


class AuthorizationInfo(BaseModel):
    """Safe authorization info returned by the proof endpoint.

    Contains no JWT claims, tokens, or internal secrets.
    """

    organization_id: int
    role: str
    capabilities: list[str]
    # Phase 1Q: lifecycle of the organization. Until it is ``active`` the
    # capabilities above are the setup subset of what the role grants.
    organization_status: str = "active"
