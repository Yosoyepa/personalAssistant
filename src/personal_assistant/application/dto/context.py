"""Application request context and token-budget DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, computed_field, model_validator

from personal_assistant.application.dto.base import ApplicationDTO
from personal_assistant.domain.common.identity import Principal


class TokenBudget(ApplicationDTO):
    """Token accounting for a request, task, or worker step."""

    limit: int = Field(gt=0, le=10_000_000)
    used: int = Field(default=0, ge=0)
    reserved: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_budget(self) -> "TokenBudget":
        if self.used + self.reserved > self.limit:
            raise ValueError("used plus reserved tokens cannot exceed limit")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def remaining(self) -> int:
        return self.limit - self.used - self.reserved

    def can_spend(self, tokens: int) -> bool:
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        return tokens <= self.remaining

    def spend(self, tokens: int) -> "TokenBudget":
        if not self.can_spend(tokens):
            raise ValueError("token budget exceeded")
        return self.model_copy(update={"used": self.used + tokens})

    def reserve(self, tokens: int) -> "TokenBudget":
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        if tokens > self.remaining:
            raise ValueError("token budget exceeded")
        return self.model_copy(update={"reserved": self.reserved + tokens})


class RequestContext(ApplicationDTO):
    """Per-request context shared by API and worker layers."""

    request_id: UUID = Field(default_factory=uuid4)
    principal: Principal
    token_budget: TokenBudget
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    channel: Literal["api", "worker", "cli", "system"] = "api"
    metadata: dict[str, str] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tenant_id(self) -> str:
        return self.principal.tenant_id
