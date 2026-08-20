from pydantic import BaseModel


class JobAccepted(BaseModel):
    event_id: str
    job_id: int
    duplicate: bool
    status: str
