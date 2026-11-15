"""Shared schema helpers."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator

Password = Annotated[str, Field(min_length=12, max_length=128)]
LoginPassword = Annotated[str, Field(min_length=1, max_length=128)]
CountryCode = Annotated[str, Field(min_length=2, max_length=2)]

# Self-service registration accepts a slightly weaker secret than the
# invitation flow (``Password``): at least 10 characters containing a letter
# and a number. The value is the single source of truth for the API, the web
# client and the tests.
REGISTER_PASSWORD_MIN_LENGTH = 10


def validate_register_password(value: str) -> str:
    """Enforce the registration password policy."""
    if len(value) < REGISTER_PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Password must be at least {REGISTER_PASSWORD_MIN_LENGTH} characters"
        )
    if not any(char.isalpha() for char in value):
        raise ValueError("Password must contain at least one letter")
    if not any(char.isdigit() for char in value):
        raise ValueError("Password must contain at least one number")
    return value


def _validate_password(value: str) -> str:
    if not value.strip():
        raise ValueError("Password must not be blank")
    return value


def _validate_country(value: str | None) -> str | None:
    if value is None:
        return None
    code = value.strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError("Country must be a two-letter ISO code")
    return code


class PasswordMixin:
    @field_validator("password")
    @classmethod
    def _password_not_blank(cls, value: str) -> str:
        return _validate_password(value)


class CountryMixin:
    @field_validator("country", check_fields=False)
    @classmethod
    def _normalise_country(cls, value: str | None) -> str | None:
        return _validate_country(value)
