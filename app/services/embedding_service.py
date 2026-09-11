from langchain_openai import OpenAIEmbeddings
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import get_logger

EXPECTED_EMBEDDING_DIMENSIONS = 1536

log = get_logger(__name__)


def validate_embedding_dimensions() -> None:
    configured = settings.embedding_dimensions
    if configured != EXPECTED_EMBEDDING_DIMENSIONS:
        raise RuntimeError(
            f"Embedding dimension mismatch: configured={configured}, "
            f"database schema requires VECTOR({EXPECTED_EMBEDDING_DIMENSIONS}). "
            f"Update alembic migration and schema to change vector dimensions."
        )


class EmbeddingService:
    def __init__(self) -> None:
        validate_embedding_dimensions()
        self.client = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=SecretStr(settings.openai_api_key),
            dimensions=settings.embedding_dimensions,
        )

    async def embed_text(
        self,
        text: str,
    ) -> list[float]:

        return await self.client.aembed_query(text)

    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:

        return await self.client.aembed_documents(texts)


embedding_service = EmbeddingService()
