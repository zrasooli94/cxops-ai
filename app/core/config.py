from datetime import datetime
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
    # Phase 1S: bound every OpenAI call. The SDK default is a 10 minute timeout
    # with 2 retries, so one hung connection could hold a customer's request,
    # a pooled database connection's turn lease and an analysis slot for the
    # better part of an hour. A turn that exceeds this fails closed instead.
    openai_request_timeout_seconds: float = 30.0
    openai_max_retries: int = 1

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

    @model_validator(mode="after")
    def _reject_debug_in_production(self) -> "Settings":
        """DEBUG=true leaks stack traces and SQL to HTTP responses and logs.

        The pilot customer is a third party, so a misconfigured deploy must
        fail at boot rather than serve internal error detail to the widget.
        """
        if self.environment == "production" and self.debug:
            raise ValueError(
                "DEBUG cannot be enabled when ENVIRONMENT=production"
            )
        return self

    @model_validator(mode="after")
    def _require_https_public_urls_in_production(self) -> "Settings":
        """Embed snippets and pilot links are customer-facing.

        A non-HTTPS public URL in production would emit insecure embed markup and
        leak session-bearing query strings over plaintext, so the app refuses to
        boot rather than hand an operator a broken pilot.
        """
        if self.environment != "production":
            return self
        for label, value in (
            ("FRONTEND_BASE_URL", self.frontend_base_url),
            ("BACKEND_PUBLIC_URL", self.backend_public_url),
        ):
            if not value.startswith("https://"):
                raise ValueError(f"{label} must use https in production")
        return self

    # Phase 1R: identical query text embeds to the identical vector, so a
    # grounded turn (which retrieves for the agent and again for the answer)
    # reuses one provider call. Process-local, keyed by a digest, never
    # persisted; 0 disables it.
    embedding_query_cache_size: int = 128
    embedding_query_cache_ttl_seconds: float = 300.0

    # Phase 1R: the start-up schema check runs before the port binds, so a
    # hung database connect would hold the whole service unreachable for the
    # driver's default 60 s. Short attempts with a retry recover from a
    # scaling-to-zero database much sooner than one long wait.
    schema_gate_attempt_timeout_seconds: float = 10.0
    schema_gate_max_attempts: int = 4
    schema_gate_retry_delay_seconds: float = 1.0

    rag_top_k: int = 5
    rag_min_similarity: float = 0.35
    rag_similarity_margin: float = 0.12
    rag_max_sources: int = 3

    llm_input_cost_per_million: float = 0.15
    llm_output_cost_per_million: float = 0.60

    support_hourly_cost_usd: float = 25.0
    minutes_saved_per_autonomous_execution: float = 8.0
    roi_min_autonomous_samples: int = 20

    # Deployment topology (Phase 1P.3). Both are operator-facing public URLs
    # used to build embed snippets and to print links in onboarding output.
    # They are never used for authorization and carry no credentials.
    frontend_base_url: str = "http://127.0.0.1:3000"
    backend_public_url: str = "http://127.0.0.1:8000"

    @field_validator("frontend_base_url", "backend_public_url", mode="after")
    @classmethod
    def _require_absolute_base_url(cls, value: str) -> str:
        candidate = value.strip().rstrip("/")
        parts = urlsplit(candidate)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(
                "base URLs must be absolute http(s) origins without a path, "
                "for example https://app.example.com"
            )
        if parts.path or parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError(
                "base URLs must not include a path, query, fragment, or credentials"
            )
        return candidate

    # Phase 1U-A: restricted RISPU email + Telegram pilot, OFF by default.
    # All transport URLs stay in server env secrets, never user-controlled.
    handoff_notifications_enabled: bool = False
    handoff_notification_pilot_tenant: str = "rispu"
    handoff_notification_activated_at: datetime | None = None
    handoff_notification_email_url: str = ""
    handoff_notification_telegram_url: str = ""
    handoff_notification_max_per_hour: int = 6

    # Public web chat (Phase 1P.1)
    public_chat_session_token_entropy_bytes: int = 32
    public_chat_max_active_sessions_per_config: int = 2000
    public_chat_max_messages_per_minute_per_config: int = 600
    public_chat_new_sessions_per_hour_per_config: int = 120

    # Phase 1P.9: asynchronous public-chat turns. The visitor's message is
    # accepted and persisted immediately; the AI turn runs as a durable,
    # tenant-bound job processed in-process by the API (bounded concurrency)
    # with the scheduled worker as a backstop.
    #
    # Phase 1P.9R rollout gate: a turn is asynchronous only when ALL of
    #   1. this platform switch is on (default OFF, so merging the code never
    #      changes production behaviour),
    #   2. the tenant's ``public_chat_configurations.async_turns_enabled`` is
    #      on (default OFF; RISPU-first activation), and
    #   3. the widget asks for ``delivery: "async"``.
    # Turning this off is the kill switch: every request is served
    # synchronously again, and turns already queued still complete.
    public_chat_async_enabled: bool = False
    # Concurrent AI turns run by one API process. A running turn holds one
    # pooled connection across the model call (its lease connection, which also
    # carries the analysis lock); its job session is released around model
    # calls. Keep this below the pool size (see agent_analysis_max_concurrency).
    # 6 is the measured optimum on a 0.5-CPU / 512 MB instance with the default
    # 5 + 10 pool (Phase 1P.9R certification): vs 4, queue-wait p95 -24% at the
    # same CPU and pool waits; 8 is no faster because the analysis slots
    # (pool - turns - 2 = 5) become the limit.
    public_chat_turn_max_concurrency: int = 6
    public_chat_turn_max_concurrency_per_tenant: int = 2
    # A turn still "processing" after this long is treated as abandoned (API
    # instance restarted mid-turn) and may be re-claimed. Reply de-duplication
    # guarantees a re-run can never post a second customer message.
    public_chat_turn_stale_seconds: int = 120
    # Phase 1P.9R: a low-frequency in-process sweep re-dispatches turns nobody
    # is waiting on (e.g. after a crash with the visitor gone). One bounded,
    # indexed query per interval; 0 disables. Hourly keeps Neon free-plan
    # compute impact negligible (the database may wake at most once per hour).
    public_chat_turn_sweep_seconds: int = 3600
    # Default monthly AI allowance per tenant when its widget configuration has
    # no override. Exhausting either limit routes new turns to a human (no
    # model call); messages are still accepted.
    public_chat_default_monthly_ai_turns: int = 3000
    public_chat_default_monthly_ai_budget_usd: float = 5.0
    # Phase 1P.9R: estimated cost reserved before each AI turn (decision + RAG
    # + embeddings + retries) and held until the turn settles. Deliberately
    # above the typical turn cost so concurrent turns cannot overshoot a budget.
    public_chat_turn_reserved_cost_usd: float = 0.01
    # A reservation not settled within this window (crashed turn) stops being
    # counted as outstanding; any model calls it made are already logged.
    public_chat_reservation_ttl_seconds: int = 900
    # Platform-wide monthly AI ceiling across all tenants (estimated). 0 means
    # no AI turns at all. Keep it at or below the provider-side hard limit.
    platform_monthly_ai_budget_usd: float = 15.0
    # Phase 1P.9R: concurrent persisted agent analyses per process (sync chat,
    # async turns, staff analysis). Each holds one dedicated lock connection
    # for its duration; bounding them keeps the pool from deadlocking.
    agent_analysis_max_concurrency: int = 8

    # Phase 1Q: commercial onboarding.
    #
    # Public early-access / demo-request submissions. Default OFF: releasing
    # lead capture in production is a separate owner approval. While off, the
    # public endpoint answers 503 and stores nothing.
    public_lead_capture_enabled: bool = False
    # Abuse ceilings for lead submissions. The platform-wide cap is the robust
    # bound (it does not depend on a client IP the API cannot verify); the
    # per-email cap stops one address from flooding the review queue.
    lead_submissions_per_hour_platform: int = 30
    lead_submissions_per_day_per_email: int = 3
    # Team invitations: single-use, hashed at rest, and short-lived.
    team_invitation_ttl_hours: int = 72
    # Knowledge ingestion calls the paid embedding API. A tenant that has not
    # been approved (lifecycle_status != active) may upload knowledge during
    # setup only when this is on. Default OFF: approval gates every AI spend.
    onboarding_knowledge_setup_enabled: bool = False
    # Phase 1Q-R: evidence that the person accepting an invitation owns the
    # invited, verified email address. "disabled" (default) refuses every
    # acceptance; "nhost_userinfo" asks Nhost Auth's /user endpoint with the
    # caller's own token; "jwt_claims" requires signed email + email_verified
    # claims. See app/services/identity_verification.py.
    invitation_identity_mode: str = "disabled"
    # Nhost Auth base URL, e.g. https://<subdomain>.auth.<region>.nhost.run/v1
    nhost_auth_url: str = ""
    invitation_identity_timeout_seconds: float = 5.0
    # Phase 1Q-R: how many not-yet-approved organizations one identity may
    # create through self-service POST /organizations (abuse / name squatting).
    self_service_organization_limit: int = 2
    # Phase 1Q-R: estimated cost reserved before a staff-triggered AI call
    # and held until it finishes (its logged cost then replaces the estimate).
    # Evaluation runs make many model calls, so they reserve more.
    staff_ai_reserved_cost_usd: float = 0.02
    staff_ai_evaluation_reserved_cost_usd: float = 0.5
    # Optional public address shown to prospects while lead capture is off.
    # Empty = no address shown (the owner has not provided one).
    public_contact_email: str = ""

    @field_validator("invitation_identity_mode", mode="after")
    @classmethod
    def _identity_mode_known(cls, value: str) -> str:
        if value not in {"disabled", "nhost_userinfo", "jwt_claims"}:
            raise ValueError(
                "invitation_identity_mode must be disabled, nhost_userinfo or jwt_claims"
            )
        return value

    @field_validator("nhost_auth_url", mode="after")
    @classmethod
    def _nhost_auth_url_https(cls, value: str) -> str:
        if not value:
            return value
        parts = urlsplit(value)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("nhost_auth_url must not contain credentials, a query or a fragment")
        if not parts.hostname:
            raise ValueError("nhost_auth_url must include a host")
        if parts.scheme != "https" and not (
            parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}
        ):
            raise ValueError("nhost_auth_url must use https")
        return value.rstrip("/")

    @field_validator(
        "lead_submissions_per_hour_platform",
        "lead_submissions_per_day_per_email",
        "team_invitation_ttl_hours",
        mode="after",
    )
    @classmethod
    def _commercial_limits_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Lead and invitation limits must be positive")
        return value

    @field_validator("public_chat_session_token_entropy_bytes", mode="after")
    @classmethod
    def _public_chat_token_entropy_bounded(cls, value: int) -> int:
        if not 16 <= value <= 64:
            raise ValueError(
                "public_chat_session_token_entropy_bytes must be between 16 and 64"
            )
        return value

    @field_validator(
        "public_chat_max_active_sessions_per_config",
        "public_chat_max_messages_per_minute_per_config",
        "public_chat_new_sessions_per_hour_per_config",
        mode="after",
    )
    @classmethod
    def _public_chat_limits_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Public chat per-config limits must be positive")
        return value

    @field_validator(
        "public_chat_turn_max_concurrency",
        "public_chat_turn_max_concurrency_per_tenant",
        "public_chat_turn_stale_seconds",
        "public_chat_default_monthly_ai_turns",
        "public_chat_reservation_ttl_seconds",
        "agent_analysis_max_concurrency",
        mode="after",
    )
    @classmethod
    def _public_chat_turn_limits_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Public chat turn limits must be positive")
        return value

    @field_validator(
        "public_chat_default_monthly_ai_budget_usd",
        "public_chat_turn_reserved_cost_usd",
        "staff_ai_reserved_cost_usd",
        "staff_ai_evaluation_reserved_cost_usd",
        "platform_monthly_ai_budget_usd",
        mode="after",
    )
    @classmethod
    def _public_chat_budget_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("AI budget settings must be >= 0")
        return value

    @field_validator("public_chat_turn_sweep_seconds", mode="after")
    @classmethod
    def _public_chat_sweep_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("public_chat_turn_sweep_seconds must be >= 0 (0 disables)")
        return value

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
