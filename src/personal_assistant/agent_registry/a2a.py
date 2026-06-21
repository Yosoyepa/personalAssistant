"""A2A-compatible contracts prepared for future interoperability."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class A2AModel(BaseModel):
    """Base model with JSON helpers for A2A artifacts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, payload: str) -> "A2AModel":
        return cls.model_validate_json(payload)


class AgentSkill(A2AModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class AgentCard(A2AModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    endpoint: str | None = None
    skills: list[AgentSkill] = Field(default_factory=list)
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain", "application/json"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["application/json"])


class MessageRole(str, Enum):
    user = "user"
    agent = "agent"
    tool = "tool"


class Message(A2AModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    role: MessageRole
    parts: list[dict[str, Any]]
    tenant_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Artifact(A2AModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    artifact_id: str = Field(default_factory=lambda: f"art_{uuid4().hex}")
    name: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str = Field(min_length=1)


class AgentTask(A2AModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex}")
    agent_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    messages: list[Message] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def personal_assistant_card() -> AgentCard:
    """Return a valid card without publishing external discovery."""

    return AgentCard(
        agent_id="personal_assistant",
        name="Personal Assistant",
        description="Tenant-scoped personal assistant with L2 workflows for reminders, documents, and memory.",
        version="0.1.0",
        endpoint=None,
        skills=[
            AgentSkill(
                id="reminder.create",
                name="Create Reminder",
                description="Extracts a reminder request and prepares approved calendar/reminder state.",
                version="0.1.0",
            )
        ],
    )
