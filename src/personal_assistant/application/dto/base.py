"""Base classes for application DTOs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApplicationDTO(BaseModel):
    """Base model config for application boundary DTOs."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
        populate_by_name=True,
        use_enum_values=False,
    )
