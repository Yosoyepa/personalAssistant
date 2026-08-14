"""WhatsApp webhook response and request models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from personal_assistant.application.dto.runtime import AgentStatus


class WhatsAppWebhookResponse(BaseModel):
    """Payload returned for incoming WhatsApp Cloud API webhooks."""

    model_config = ConfigDict(extra="forbid")

    status: AgentStatus = AgentStatus.completed
    reply: str | None = None
    sent: bool = False
    approval_id: str | None = None
    command: str | None = None
