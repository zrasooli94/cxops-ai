from pydantic import BaseModel, Field


class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=500,
    )

    content: str = Field(
        min_length=1,
    )

    source: str = "manual"
    source_uri: str | None = None
    metadata: dict = Field(default_factory=dict)


class KnowledgeIngestionResult(BaseModel):
    document_id: int
    title: str
    chunks_created: int
    duplicate: bool


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
    )


class KnowledgeSearchResult(BaseModel):
    chunk_id: int
    document_id: int
    content: str
    distance: float
    similarity: float
    metadata: dict