from langchain_openai import OpenAIEmbeddings
from pydantic import SecretStr

from app.core.config import settings


class EmbeddingService:
    def __init__(self) -> None:
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
