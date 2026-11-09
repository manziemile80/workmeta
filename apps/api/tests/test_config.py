"""Configuration parsing tests (no external services required)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_cors_origins_accept_comma_separated_value() -> None:
    settings = Settings(cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_log_level_is_normalised() -> None:
    assert Settings(log_level="debug").log_level == "DEBUG"


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(log_level="verbose")


def test_production_flag() -> None:
    production = Settings(
        app_env="production",
        session_secret="0123456789abcdef0123456789abcdef0123456789abcdef",
        database_url="postgresql+asyncpg://wm_app:real-secret@db:5432/workmeta",
        database_migration_url="postgresql+asyncpg://workmeta_owner:real-secret@db:5432/workmeta",
        billing_provider="flutterwave",
        allowed_hosts=["api.example.com"],
    )
    assert production.is_production is True
    assert Settings(app_env="development").is_production is False


def test_production_requires_strong_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", session_secret="too-short")
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            session_secret="0123456789abcdef0123456789abcdef0123456789abcdef",
            database_url="postgresql+asyncpg://wm_app:change-me@db:5432/workmeta",
            database_migration_url="postgresql+asyncpg://workmeta_owner:change-me@db:5432/workmeta",
            billing_provider="flutterwave",
            allowed_hosts=["api.example.com"],
        )
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            session_secret="0123456789abcdef0123456789abcdef0123456789abcdef",
            database_url="postgresql+asyncpg://wm_app:real-secret@db:5432/workmeta",
            database_migration_url="postgresql+asyncpg://workmeta_owner:real-secret@db:5432/workmeta",
            billing_provider="mock",
            allowed_hosts=["api.example.com"],
        )
