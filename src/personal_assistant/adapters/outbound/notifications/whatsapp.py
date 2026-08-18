"""WhatsApp outbound notification adapter facade.

Facade module: implementation is split into focused siblings (models, client, tool)
to keep each file under the mutation-site limit while preserving public imports.
"""

from __future__ import annotations

from personal_assistant.adapters.outbound.notifications.whatsapp_client import (
    WhatsAppGraphApiClient,
)
from personal_assistant.adapters.outbound.notifications.whatsapp_models import (
    WhatsAppClient,
    WhatsAppProviderResult,
)
from personal_assistant.adapters.outbound.notifications.whatsapp_tool import (
    WhatsAppNotificationTool,
)

__all__ = [
    "WhatsAppClient",
    "WhatsAppGraphApiClient",
    "WhatsAppNotificationTool",
    "WhatsAppProviderResult",
]
