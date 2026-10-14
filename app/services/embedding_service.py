import hashlib
import time
from collections import OrderedDict

from pydantic import SecretStr

from app.core.config import settings
from app.core.lazy import LazyAttribute
from app.core.logging import get_logger
from app.core.metrics import record_embedding_cache

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


class QueryEmbeddingCache:
    """Small process-local TTL/LRU memo of query embeddings.

    The key is a digest of (model, dimensions, text); the text itself is not
    retained. An embedding is a pure function of those inputs, so serving the
    stored vector can never cross a tenant boundary: every tenant gets exactly
    what the provider would have returned for the same string.
    """

    def __init__(self, max_entries: int, ttl_seconds: float) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()

    @staticmethod
    def key(text: str) -> str:
        material = f"{settings.embedding_model}|{settings.embedding_dimensions}|{text}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, key: str) -> list[float] | None:
        if self.max_entries <= 0:
            return None
        entry = self._items.get(key)
        if entry is None:
            return None
        stored_at, vector = entry
        if time.monotonic() - stored_at > self.ttl_seconds:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return vector

    def put(self, key: str, vector: list[float]) -> None:
        if self.max_entries <= 0:
            return
        self._items[key] = (time.monotonic(), vector)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)


class EmbeddingService:
    def __init__(self) -> None:
        validate_embedding_dimensions()
        self.query_cache = QueryEmbeddingCache(
            settings.embedding_query_cache_size,
            settings.embedding_query_cache_ttl_seconds,
        )

    @LazyAttribute
    def client(self):
        """The embeddings client, built on first use (see ``RAGService.llm``)."""
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=SecretStr(settings.openai_api_key),
            dimensions=settings.embedding_dimensions,
            timeout=settings.openai_request_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )

    async def embed_text(
        self,
        text: str,
    ) -> list[float]:

        key = self.query_cache.key(text)
        cached = self.query_cache.get(key)
        if cached is not None:
            record_embedding_cache(result="hit")
            return list(cached)
        record_embedding_cache(result="miss")
        vector = await self.client.aembed_query(text)
        self.query_cache.put(key, list(vector))
        return vector

    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:

        return await self.client.aembed_documents(texts)


embedding_service = EmbeddingService()
