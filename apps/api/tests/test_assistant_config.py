"""Unit tests for assistant model and retrieval configuration contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.assistant import AssistantCreate, AssistantUpdate, ModelOptions


def test_model_options_accept_valid_overrides_and_strip_the_model_name() -> None:
    options = ModelOptions(
        model="  gpt-4.1-mini  ",
        temperature=0.7,
        max_output_tokens=512,
    )
    assert options.model == "gpt-4.1-mini"
    assert options.temperature == 0.7
    assert options.max_output_tokens == 512


@pytest.mark.parametrize(
    "payload",
    [
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"max_output_tokens": 32},
        {"max_output_tokens": 5000},
        {"model": "   "},
        {"unexpected": True},
    ],
)
def test_model_options_reject_unsafe_values(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ModelOptions(**payload)


def test_assistant_create_keeps_only_explicit_model_options() -> None:
    created = AssistantCreate(name="Assistant")
    assert created.model_options.model_dump(exclude_unset=True) == {}

    configured = AssistantCreate(
        name="Assistant", model_options={"temperature": 0.2}
    )
    assert configured.model_options.model_dump(exclude_unset=True) == {
        "temperature": 0.2
    }


def test_assistant_update_allows_partial_model_options() -> None:
    update = AssistantUpdate(model_options={"max_output_tokens": 300})
    dump = update.model_dump(exclude_unset=True)
    assert dump == {"model_options": {"max_output_tokens": 300}}
