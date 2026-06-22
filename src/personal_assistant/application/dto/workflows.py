"""Durable-lite workflow state DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class WorkflowStatus(str, Enum):
    pending = "pending"
    running = "running"
    waiting_approval = "waiting_approval"
    completed = "completed"
    failed = "failed"


class WorkflowState(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workflow_id: str = Field(default_factory=lambda: f"wf_{uuid4().hex}")
    tenant_id: str = Field(min_length=1)
    workflow_type: str = Field(min_length=1)
    status: WorkflowStatus = WorkflowStatus.pending
    step: str = "created"
    idempotency_key: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def transition(
        self,
        *,
        status: WorkflowStatus | None = None,
        step: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> "WorkflowState":
        merged = dict(self.data)
        if data:
            merged.update(data)
        return self.model_copy(
            update={
                "status": status or self.status,
                "step": step or self.step,
                "data": merged,
                "updated_at": datetime.now(UTC),
            }
        )


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_seconds: float = Field(default=1.0, ge=0)
    timeout_seconds: float = Field(default=30.0, gt=0)
