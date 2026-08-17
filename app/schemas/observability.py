from pydantic import BaseModel


class AIObservabilitySummary(BaseModel):
    total_requests: int
    success_rate: float
    grounded_rate: float
    avg_latency_ms: float
    total_tokens: int
    estimated_cost_usd: float