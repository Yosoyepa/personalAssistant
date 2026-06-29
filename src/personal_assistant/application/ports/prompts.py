"""Prompt catalog application port."""

from __future__ import annotations

from typing import Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field


class RenderedPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    text: str = Field(min_length=1)


class PromptCatalogPort(Protocol):
    def render(self, prompt_id: str, variables: Mapping[str, object]) -> RenderedPrompt:
        """Render one versioned prompt from trusted application variables."""
