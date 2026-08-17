from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CXOps AI"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True

    database_url: str = (
        "postgresql+asyncpg://cxops:cxops@localhost:5432/cxops"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    zendesk_webhook_secret: str = ""
    zendesk_oauth_token: str = ""

    zendesk_subdomain: str = ""
    zendesk_client_id: str = ""
    zendesk_client_secret: str = ""
    zendesk_redirect_uri: str = (
        "http://127.0.0.1:8000/auth/zendesk/callback"
    )
    zendesk_oauth_scope: str = "read write"

    openai_api_key: str = ""

    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    chat_model: str = "gpt-5.6-luna"
    rag_top_k: int = 5
    rag_min_similarity: float = 0.35
    rag_similarity_margin: float = 0.12
    rag_max_sources: int = 3

@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()