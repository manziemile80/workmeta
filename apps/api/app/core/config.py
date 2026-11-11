"""Application configuration.

Every value is read from environment variables (or the repository `.env` file).
Secrets never have real defaults: development placeholders are intentionally
obvious, and production requires them to be provided explicitly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _repo_root() -> Path:
    """Locate the repository root without depending on a fixed depth.

    A source checkout is ``<repo>/apps/api/app/core/config.py`` (five levels).
    The container image flattens that to ``/app/app/core/config.py``, which has
    fewer ancestors and no monorepo layout, so fall back to the directory that
    contains ``app`` (``/app``).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "apps" / "api" / "pyproject.toml").is_file():
            return parent
    parents = here.parents
    return parents[2] if len(parents) >= 3 else here.parent


REPO_ROOT = _repo_root()
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "WorkMeta AI API"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "production"] = "development"
    api_v1_prefix: str = "/api/v1"
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    # Host allow-list for TrustedHostMiddleware. "*" allows any host (dev/tests
    # and direct-IP deployments); production should list the public hostnames.
    allowed_hosts: Annotated[list[str], NoDecode] = ["*"]
    # Explicit HTTPS hardening overrides. ``None`` keeps the environment-derived
    # default (on only for APP_ENV=production). Free-tier staging runs as
    # ``development`` because BILLING_PROVIDER=mock is rejected in production,
    # yet it is still served over HTTPS, so these can be switched on directly.
    cookie_secure: bool | None = None
    enable_docs: bool | None = None
    enable_hsts: bool | None = None
    # Enforce the production invariants (strong session secret, no placeholder
    # database URL, explicit host and CORS allow-lists) even when APP_ENV is
    # ``development``. Useful where the environment name cannot be flipped yet.
    enforce_production_checks: bool | None = None
    # Log rendering: ``auto`` (JSON in production, console otherwise), ``json``
    # or ``console``.
    log_format: Literal["auto", "json", "console"] = "auto"
    # Total JSON/body size cap. Uploads have their own per-file limit as well.
    max_request_body_bytes: int = 12 * 1024 * 1024

    # Rate limiting for unauthenticated / sensitive endpoints (per client IP).
    # Defaults are intentionally generous: the reverse proxy is the
    # authoritative limiter in production. This in-memory layer stops naive
    # floods (and, when tightened via settings, slows brute force). It is
    # per-process and resets on restart.
    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = 1000
    rate_limit_invite_accept_per_minute: int = 1000
    rate_limit_webhook_per_minute: int = 1000
    rate_limit_chat_per_minute: int = 1000

    # Database. The runtime role is intentionally restricted (no BYPASSRLS).
    database_url: str = (
        "postgresql+asyncpg://wm_app:change-me@127.0.0.1:5432/workmeta"
    )
    database_migration_url: str = (
        "postgresql+asyncpg://workmeta_owner:change-me@127.0.0.1:5432/workmeta"
    )

    # Sessions (used from Stage 1 onward)
    session_secret: str = ""
    session_ttl_days: int = 7
    session_idle_hours: int = 8
    invitation_ttl_days: int = 7
    password_reset_ttl_minutes: int = 60
    backup_retention_days: int = 30

    # AI provider — server-side only. The key is never returned by the API.
    ai_provider: str = "openai"
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_chat_model: str = "gpt-4.1-mini"
    ai_embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    ai_timeout_seconds: int = 60
    ai_temperature: float = 0.0
    ai_max_retries: int = 2
    ai_retry_backoff_seconds: float = 0.5
    ai_retry_max_backoff_seconds: float = 8.0
    chat_max_output_tokens: int = 800
    ai_supported_chat_models: Annotated[list[str], NoDecode] = [
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4o-mini",
    ]

    # Estimated provider pricing in USD per million tokens. These are estimates
    # for reporting only; invoices always come from the provider.
    ai_chat_input_cost_per_million: float = 0.40
    ai_chat_output_cost_per_million: float = 1.60
    ai_embedding_cost_per_million: float = 0.02

    # Knowledge ingestion and retrieval
    max_upload_bytes: int = 10 * 1024 * 1024
    max_document_pages: int = 200
    chunk_tokens: int = 900
    chunk_overlap_tokens: int = 150
    embedding_batch_size: int = 64
    retrieval_top_k: int = 6
    retrieval_min_score: float = 0.2
    ingestion_max_attempts: int = 3
    worker_poll_seconds: float = 2.0

    # OCR (optional). "none" keeps image/scanned-PDF uploads disabled.
    ocr_engine: Literal["none", "tesseract"] = "none"
    ocr_language: str = "eng"
    ocr_timeout_seconds: int = 30
    ocr_max_image_pixels: int = 40_000_000
    ocr_pdf_dpi: int = 200

    # Streaming
    stream_heartbeat_seconds: float = 15.0

    # Billing and subscriptions. Plans themselves live in the database.
    # The mock provider is for development/tests only; production must be
    # configured with a real gateway and secrets.
    billing_provider: Literal["mock", "flutterwave", "paystack"] = "mock"
    billing_currency: str = "RWF"
    billing_trial_days: int = 14
    billing_past_due_grace_days: int = 7
    billing_checkout_callback_url: str = "http://localhost:5173/billing/checkout"
    billing_mock_webhook_secret: str = "dev-mock-webhook-secret"
    flutterwave_base_url: str = "https://api.flutterwave.com/v3"
    flutterwave_secret_key: str = ""
    flutterwave_webhook_hash: str = ""
    paystack_base_url: str = "https://api.paystack.co"
    paystack_secret_key: str = ""
    paystack_webhook_secret: str = ""
    # When true (default), signed webhook "succeeded" events are confirmed with
    # a provider verify round-trip before the plan is activated — the same
    # check as checkout confirmation. Mock is exempt (no network).
    billing_webhook_verify: bool = True

    # Storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: str = "./var/storage"

    # Extension signing
    extension_signing_key_id: str = "dev-1"
    extension_signing_private_key: str = ""

    # First-run owner
    seed_owner_email: str = ""
    seed_owner_password: str = ""

    # Observability
    sentry_dsn: str = ""

    @field_validator("cors_origins", "ai_supported_chat_models", "allowed_hosts", mode="before")
    @classmethod
    def _split_list_values(cls, value: object) -> object:
        """Accept a comma-separated environment value as well as a JSON list."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("database_url", "database_migration_url", mode="before")
    @classmethod
    def _normalise_db_url(cls, value: object) -> object:
        """Make Neon connection strings asyncpg-compatible.

        asyncpg rejects ``channel_binding`` and knows ``ssl`` (not ``sslmode``).
        Neon strings carry ``?sslmode=require&channel_binding=require``; the
        pooler endpoint otherwise works once channel binding is dropped.
        """
        if not isinstance(value, str):
            return value
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        parsed = urlparse(value)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params.pop("channel_binding", None)
        if "sslmode" in params and "ssl" not in params:
            params["ssl"] = params.pop("sslmode")
        params.pop("sslmode", None)
        query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=query))

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @field_validator("chunk_overlap_tokens")
    @classmethod
    def _overlap_smaller_than_chunk(cls, value: int, info: ValidationInfo) -> int:
        chunk_tokens = info.data.get("chunk_tokens", 900)
        if value < 0 or value >= chunk_tokens:
            raise ValueError("chunk_overlap_tokens must be >= 0 and < chunk_tokens")
        return value

    @field_validator("max_upload_bytes", "embedding_dimensions")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value

    @field_validator("ai_timeout_seconds")
    @classmethod
    def _valid_timeout(cls, value: int) -> int:
        if not 1 <= value <= 600:
            raise ValueError("ai_timeout_seconds must be between 1 and 600")
        return value

    @field_validator("chat_max_output_tokens")
    @classmethod
    def _valid_output_tokens(cls, value: int) -> int:
        if not 1 <= value <= 8192:
            raise ValueError("chat_max_output_tokens must be between 1 and 8192")
        return value

    @field_validator("ai_temperature")
    @classmethod
    def _valid_temperature(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError("ai_temperature must be between 0 and 2")
        return value

    @field_validator("ai_max_retries")
    @classmethod
    def _valid_retry_count(cls, value: int) -> int:
        if not 0 <= value <= 10:
            raise ValueError("ai_max_retries must be between 0 and 10")
        return value

    @field_validator("ai_retry_backoff_seconds", "ai_retry_max_backoff_seconds")
    @classmethod
    def _non_negative_backoff(cls, value: float) -> float:
        if value < 0:
            raise ValueError("retry backoff must be non-negative")
        return value

    @field_validator("retrieval_top_k")
    @classmethod
    def _reasonable_top_k(cls, value: int) -> int:
        if not 1 <= value <= 50:
            raise ValueError("retrieval_top_k must be between 1 and 50")
        return value

    @field_validator("retrieval_min_score")
    @classmethod
    def _valid_score(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("retrieval_min_score must be between 0 and 1")
        return value

    @field_validator(
        "ai_chat_input_cost_per_million",
        "ai_chat_output_cost_per_million",
        "ai_embedding_cost_per_million",
    )
    @classmethod
    def _non_negative_cost(cls, value: float) -> float:
        if value < 0:
            raise ValueError("cost estimates must be non-negative")
        return value

    @field_validator("ocr_timeout_seconds", "ocr_max_image_pixels", "ocr_pdf_dpi")
    @classmethod
    def _positive_ocr(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("OCR settings must be positive")
        return value

    @field_validator("stream_heartbeat_seconds")
    @classmethod
    def _non_negative_heartbeat(cls, value: float) -> float:
        if value < 0:
            raise ValueError("stream_heartbeat_seconds must be non-negative")
        return value

    @field_validator("billing_currency")
    @classmethod
    def _valid_currency(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if len(cleaned) != 3 or not cleaned.isalpha():
            raise ValueError("billing_currency must be a 3-letter ISO code")
        return cleaned

    @field_validator("billing_trial_days", "billing_past_due_grace_days")
    @classmethod
    def _reasonable_billing_days(cls, value: int) -> int:
        if not 0 <= value <= 365:
            raise ValueError("billing day settings must be between 0 and 365")
        return value

    @field_validator("session_ttl_days", "invitation_ttl_days")
    @classmethod
    def _valid_ttl_days(cls, value: int) -> int:
        if not 1 <= value <= 30:
            raise ValueError("session/invitation TTL must be between 1 and 30 days")
        return value

    @field_validator("session_idle_hours", "password_reset_ttl_minutes", "backup_retention_days")
    @classmethod
    def _positive_hardening(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("hardening settings must be positive")
        return value

    @field_validator(
        "max_request_body_bytes",
        "rate_limit_login_per_minute",
        "rate_limit_invite_accept_per_minute",
        "rate_limit_webhook_per_minute",
        "rate_limit_chat_per_minute",
    )
    @classmethod
    def _non_negative_limits(cls, value: int) -> int:
        if value < 0:
            raise ValueError("limit settings must be non-negative")
        return value

    @property
    def resolved_storage_dir(self) -> Path:
        path = Path(self.storage_dir)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def production_checks_enforced(self) -> bool:
        """True when the production invariants must hold, whatever APP_ENV says."""
        if self.enforce_production_checks is not None:
            return self.enforce_production_checks
        return self.is_production

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.is_production

    @property
    def hsts_enabled(self) -> bool:
        if self.enable_hsts is not None:
            return self.enable_hsts
        return self.is_production

    @property
    def docs_enabled(self) -> bool:
        if self.enable_docs is not None:
            return self.enable_docs
        return self.app_env != "production"

    @model_validator(mode="after")
    def _production_secrets(self) -> Settings:
        strict = self.is_production
        if not (strict or self.production_checks_enforced):
            return self
        if len(self.session_secret.strip()) < 32:
            raise ValueError(
                "SESSION_SECRET must be at least 32 characters in production"
            )
        if "change-me" in self.database_url:
            raise ValueError("DATABASE_URL still contains the placeholder password")
        if strict:
            # The owner URL is mandatory when running in production mode.
            if "change-me" in self.database_migration_url:
                raise ValueError(
                    "DATABASE_MIGRATION_URL still contains the placeholder password"
                )
            if self.billing_provider == "mock":
                raise ValueError(
                    "BILLING_PROVIDER=mock is not allowed in production"
                )
        elif "change-me" in self.database_migration_url:
            # Enforced-but-not-production mode: an unset migration URL is fine,
            # the runtime service never executes Alembic.
            pass
        if not self.allowed_hosts or self.allowed_hosts == ["*"]:
            raise ValueError(
                "ALLOWED_HOSTS must list the public API hostnames in production"
            )
        if self.cors_origins == ["*"]:
            raise ValueError("CORS_ORIGINS must not be * in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
