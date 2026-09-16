from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.encryption import validate_encryption_keys


class Settings(BaseSettings):
    app_name: str = "CXOps AI"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True

    database_url: str = "postgresql+asyncpg://cxops:cxops@localhost:5432/cxops"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Authentication / Identity
    auth_mode: str = "hs256"
    auth_jwt_secret: str = ""
    auth_jwt_algorithm: str = "HS256"
    auth_jwt_issuer: str = ""
    auth_jwt_audience: str = ""
    auth_dev_mode: bool = False

    # JWKS / OIDC
    auth_jwks_url: str = ""
    auth_jwks_algorithms: str = "RS256"
    auth_jwks_cache_ttl_seconds: int = 600

    zendesk_webhook_secret: str = ""
    ticket_event_webhook_secret: str = ""
    zendesk_subdomain: str = ""
    zendesk_client_id: str = ""
    zendesk_client_secret: str = ""
    zendesk_redirect_uri: str = "http://127.0.0.1:8000/auth/zendesk/callback"
    zendesk_oauth_scope: str = "read write"
    zendesk_oauth_state_ttl_seconds: int = 600
    zendesk_webhook_replay_window_seconds: int = 300
    generic_webhook_replay_window_seconds: int = 300

    # Encryption at rest for Zendesk OAuth tokens and webhook secrets.
    # Comma-separated Fernet keys, primary (encryption) key first.
    encryption_keys: str = ""

    # Allow reading non-encrypted legacy secret values. Enable only during a
    # deliberate migration window; production must enforce strict ciphertext.
    encryption_allow_legacy_plaintext: bool = False

    openai_api_key: str = ""

    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    chat_model: str = "gpt-4o-mini"

    @field_validator(
        "generic_webhook_replay_window_seconds",
        "zendesk_webhook_replay_window_seconds",
        mode="after",
    )
    @classmethod
    def _require_positive_replay_window(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Replay window must be a positive number of seconds")
        return value

    @model_validator(mode="after")
    def _reject_dev_auth_in_production(self) -> "Settings":
        if self.environment == "production" and self.auth_dev_mode:
            raise ValueError(
                "AUTH_DEV_MODE cannot be enabled when ENVIRONMENT=production"
            )
        if self.environment == "production" and self.auth_mode == "hs256":
            raise ValueError(
                "AUTH_MODE=hs256 is not allowed in production; use AUTH_MODE=jwks"
            )
        if self.auth_mode == "jwks" and not self.auth_jwks_url:
            raise ValueError("AUTH_JWKS_URL is required when AUTH_MODE=jwks")
        if self.environment == "production" and not self.encryption_keys:
            raise ValueError(
                "ENCRYPTION_KEYS is required in production to protect stored credentials"
            )
        if self.encryption_keys:
            # Fail early on malformed keys rather than at first runtime encryption.
            keys = validate_encryption_keys(self.encryption_keys)
            if self.environment == "production" and len(keys) == 0:
                raise ValueError(
                    "ENCRYPTION_KEYS must contain at least one valid key in production"
                )
        return self

    rag_top_k: int = 5
    rag_min_similarity: float = 0.35
    rag_similarity_margin: float = 0.12
    rag_max_sources: int = 3

    llm_input_cost_per_million: float = 0.15
    llm_output_cost_per_million: float = 0.60

    support_hourly_cost_usd: float = 25.0
    minutes_saved_per_autonomous_execution: float = 8.0
    roi_min_autonomous_samples: int = 20

    @field_validator(
        "database_url",
        mode="before",
    )
    @classmethod
    def normalize_database_url(
        cls,
        value: str,
    ) -> str:
        if not isinstance(value, str):
            return value

        if value.startswith("postgresql://"):
            value = value.replace(
                "postgresql://",
                "postgresql+asyncpg://",
                1,
            )
        elif value.startswith("postgres://"):
            value = value.replace(
                "postgres://",
                "postgresql+asyncpg://",
                1,
            )

        parts = urlsplit(value)

        query = dict(
            parse_qsl(
                parts.query,
                keep_blank_values=True,
            )
        )

        if "sslmode" in query:
            query["ssl"] = query.pop("sslmode")

        query.pop(
            "channel_binding",
            None,
        )

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query),
                parts.fragment,
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def reset_settings_cache() -> None:
    """Clear the settings cache and re-create the settings object.

    This is primarily useful for testing when settings need to be re-configured.
    """
    global settings
    get_settings.cache_clear()
    # Encryption key material is cached by raw key string; clear it so a
    # settings reload definitely uses the new keys.
    from app.core.encryption import clear_fernet_cache

    clear_fernet_cache()
    settings = get_settings()
